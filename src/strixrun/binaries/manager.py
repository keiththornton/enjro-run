"""llama-server binaries: manifest lookup and platform detection (install lives in part B)."""

import json
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

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
