"""llama-server binaries: manifest lookup and platform detection (install lives in part B)."""

import asyncio
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import httpx

MANIFEST_PATH = Path(__file__).with_name("manifest.json")
URL_PREFIX = "https://github.com/ggml-org/llama.cpp/releases/download/"
CUDA13_MIN_DRIVER = (580, 0)
CUDA12_MIN_DRIVER = (527, 41)


class BinaryError(RuntimeError):
    pass


@dataclass(frozen=True)
class Asset:
    asset: str
    url: str
    format: str
    size: int
    sha256: str


@dataclass(frozen=True)
class VariantSpec(Asset):
    tag: str = ""
    variant: str = ""
    extras: tuple[Asset, ...] = field(default_factory=tuple)


def _asset(d: dict) -> Asset:
    if not d["url"].startswith(URL_PREFIX):
        raise BinaryError(f"manifest URL {d['url']!r} is not a llama.cpp GitHub release")
    return Asset(d["asset"], d["url"], d["format"], int(d["size"]), d["sha256"])


class Manifest:
    def __init__(self, data: dict) -> None:
        self._data = data

    @classmethod
    def load(cls, path: Path = MANIFEST_PATH) -> "Manifest":
        return cls(json.loads(Path(path).read_text()))

    @property
    def default(self) -> str:
        return self._data["default"]

    def tags(self) -> list[str]:
        return list(self._data["tags"])

    def variants(self, tag: str) -> list[str]:
        return list(self._data["tags"][tag])

    def lookup(self, tag: str | None, variant: str) -> VariantSpec:
        tag = tag or self.default
        if tag not in self._data["tags"]:
            raise BinaryError(f"llama.cpp {tag} is not in the manifest; known: {', '.join(self.tags())}")
        builds = self._data["tags"][tag]
        if variant not in builds:
            raise BinaryError(f"llama.cpp {tag} has no build for {variant}")
        d = builds[variant]
        base = _asset(d)
        return VariantSpec(
            base.asset, base.url, base.format, base.size, base.sha256,
            tag=tag, variant=variant, extras=tuple(_asset(x) for x in d.get("extras", [])),
        )


class PlatformProbe:
    def platform(self) -> tuple[str, str]:
        return platform.system(), platform.machine()

    def nvidia_driver_version(self) -> tuple[int, int] | None:
        exe = shutil.which("nvidia-smi")
        if not exe:
            return None
        try:
            out = subprocess.run([exe, "--query-gpu=driver_version", "--format=csv,noheader"],
                                 capture_output=True, text=True, timeout=5, check=False).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        m = re.match(r"\s*(\d+)\.(\d+)", out)
        return (int(m[1]), int(m[2])) if m else None


def detect_variant(probe=None) -> str:
    probe = probe or PlatformProbe()
    system, machine = probe.platform()
    machine = machine.lower()
    x64 = machine in ("x86_64", "amd64")
    if system == "Darwin":
        return "macos-arm64" if machine == "arm64" else "macos-x64"
    if system == "Windows" and x64:
        driver = probe.nvidia_driver_version()
        if driver and driver >= CUDA13_MIN_DRIVER:
            return "win-cuda-13-x64"
        if driver and driver >= CUDA12_MIN_DRIVER:
            return "win-cuda-12-x64"
        return "win-cpu-x64"
    if system == "Linux" and x64:
        return "linux-cpu-x64"
    raise BinaryError(f"no prebuilt llama-server for {system}/{machine}; set binary_path to your own build")


# ---- install ----------------------------------------------------------------------------

SMOKE_TIMEOUT_S = 60
PROGRESS_EVERY = 4 * 1024 * 1024


def _unsafe_name(name: str) -> bool:
    n = name.replace("\\", "/")
    return (
        n.startswith("/")
        or re.match(r"^[A-Za-z]:", n) is not None
        or ".." in n.split("/")
    )


def safe_extract(archive: Path, fmt: str, dest: Path) -> None:
    """Extract without letting any member (or symlink) land outside `dest`."""
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    if fmt == "tar.gz":
        import tarfile

        with tarfile.open(archive, "r:gz") as tf:
            for member in tf.getmembers():
                if _unsafe_name(member.name):
                    raise BinaryError(f"unsafe path in archive: {member.name!r}")
            try:
                tf.extractall(dest, filter="data")  # rejects links/paths escaping dest
            except tarfile.FilterError as e:
                raise BinaryError(f"unsafe archive member: {e}") from e
        return
    if fmt == "zip":
        import stat
        import zipfile

        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if _unsafe_name(info.filename):
                    raise BinaryError(f"unsafe path in archive: {info.filename!r}")
                target = (dest / info.filename).resolve()
                if target != root and root not in target.parents:
                    raise BinaryError(f"unsafe path in archive: {info.filename!r}")
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise BinaryError(f"unsafe archive member (symlink in zip): {info.filename!r}")
                zf.extract(info, dest)
                if not info.is_dir() and mode & 0o111 and os.name != "nt":
                    os.chmod(target, (mode & 0o755) | 0o600)
        return
    raise BinaryError(f"unknown archive format {fmt!r}")


def _find_server(root: Path) -> Path | None:
    name = "llama-server.exe" if os.name == "nt" else "llama-server"
    return next((p for p in sorted(root.rglob(name)) if p.is_file()), None)


class BinaryManager:
    """Installs pinned llama-server builds into bin/<tag>/ and tracks current/previous."""

    def __init__(self, bus, settings, manifest: Manifest | None = None, client_factory=None,
                 variant=None, bin_dir: Path | None = None) -> None:
        from strixrun import paths

        self.bus = bus
        self.settings = settings  # SettingsStore
        self.manifest = manifest or Manifest.load()
        self._client_factory = client_factory or (lambda: httpx.AsyncClient(follow_redirects=True, timeout=60))
        self._variant = variant or detect_variant
        self.bin_dir = Path(bin_dir or paths.bin_dir())
        self._lock = asyncio.Lock()
        self.installing: str | None = None
        self.last_error: str | None = None

    # -- state ------------------------------------------------------------------------

    def _history_path(self) -> Path:
        return self.bin_dir / "installed.json"

    def _history(self) -> dict:
        try:
            return json.loads(self._history_path().read_text())
        except (OSError, ValueError):
            return {}

    def command(self) -> list[str] | None:
        s = self.settings.current
        if s.binary_path:
            return [s.binary_path] if Path(s.binary_path).exists() else None
        if s.binary_tag:
            exe = _find_server(self.bin_dir / s.binary_tag)
            return [str(exe)] if exe else None
        return None

    def status(self) -> dict:
        s = self.settings.current
        installed = sorted(p.name for p in self.bin_dir.iterdir() if p.is_dir() and not p.name.startswith(".")) \
            if self.bin_dir.is_dir() else []
        try:
            variant = s.binary_variant or self._variant()
        except BinaryError as e:
            variant = None
            self.last_error = self.last_error or str(e)
        return {
            "current_tag": s.binary_tag,
            "previous_tag": self._history().get("previous"),
            "variant": variant,
            "override_path": s.binary_path,
            "installed": installed,
            "default_tag": self.manifest.default,
            "available_tags": self.manifest.tags(),
            "installing": self.installing,
            "last_error": self.last_error,
            "ready": self.command() is not None,
        }

    def _progress(self, phase: str, **data) -> None:
        self.bus.publish("binary.progress", {"phase": phase, "tag": self.installing, **data})

    # -- install ----------------------------------------------------------------------

    async def install(self, tag: str | None = None, variant: str | None = None) -> dict:
        spec = self.manifest.lookup(tag, variant or self.settings.current.binary_variant or self._variant())
        async with self._lock:
            self.installing, self.last_error = spec.tag, None
            staging = self.bin_dir / f".staging-{spec.tag}"
            try:
                await self._install(spec, staging)
            except BinaryError as e:
                self.last_error = str(e)
                self._progress("error", message=str(e))
                raise
            finally:
                shutil.rmtree(staging, ignore_errors=True)
                self.installing = None
        return self.status()

    async def _install(self, spec: VariantSpec, staging: Path) -> None:
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        downloads = staging.parent / f".download-{spec.tag}"
        downloads.mkdir(parents=True, exist_ok=True)
        try:
            for item in [spec, *spec.extras]:
                archive = downloads / item.asset
                await self._download(item, archive)
                self._progress("extract", asset=item.asset)
                await asyncio.to_thread(safe_extract, archive, item.format, staging)
        finally:
            shutil.rmtree(downloads, ignore_errors=True)
        exe = _find_server(staging)
        if exe is None:
            raise BinaryError(f"{spec.asset} contains no llama-server executable")
        if platform.system() == "Darwin":
            await asyncio.to_thread(
                subprocess.run, ["xattr", "-dr", "com.apple.quarantine", str(staging)],
                capture_output=True, check=False,
            )
        self._progress("smoke_test")
        await asyncio.to_thread(self._smoke_test, exe)
        final = self.bin_dir / spec.tag
        shutil.rmtree(final, ignore_errors=True)
        staging.rename(final)
        self._switch(spec.tag, spec.variant)
        self._progress("done")

    async def _download(self, item: Asset, dest: Path) -> None:
        h = hashlib.sha256()
        done = last = 0
        self._progress("download", asset=item.asset, done=0, total=item.size)
        async with self._client_factory() as client, client.stream("GET", item.url) as r:
            if r.status_code != 200:
                raise BinaryError(f"download of {item.asset} failed: HTTP {r.status_code}")
            with await asyncio.to_thread(open, dest, "wb") as f:
                async for chunk in r.aiter_bytes(1 << 20):
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if done - last >= PROGRESS_EVERY:
                        last = done
                        self._progress("download", asset=item.asset, done=done, total=item.size)
        self._progress("verify", asset=item.asset)
        if h.hexdigest() != item.sha256:
            raise BinaryError(f"checksum mismatch for {item.asset}; refusing to install")

    def _smoke_test(self, exe: Path) -> None:
        try:
            r = subprocess.run([str(exe), "--version"], capture_output=True, text=True,
                               timeout=SMOKE_TIMEOUT_S, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise BinaryError(f"llama-server did not run: {e}") from e
        output = (r.stdout + r.stderr).lower()
        if r.returncode != 0 or "version" not in output:
            raise BinaryError(f"llama-server did not run (exit {r.returncode}): {output.strip()[:300]}")

    def _switch(self, tag: str, variant: str) -> None:
        s = self.settings.current
        previous = s.binary_tag if s.binary_tag != tag else self._history().get("previous")
        s.binary_tag, s.binary_variant = tag, variant
        self.settings.save(s)
        self._history_path().write_text(json.dumps({"current": tag, "previous": previous}))

    async def rollback(self) -> dict:
        async with self._lock:
            previous = self._history().get("previous")
            if not previous or _find_server(self.bin_dir / previous) is None:
                raise BinaryError("nothing to roll back to")
            current = self.settings.current.binary_tag
            s = self.settings.current
            s.binary_tag = previous
            self.settings.save(s)
            self._history_path().write_text(json.dumps({"current": previous, "previous": current}))
        return self.status()
