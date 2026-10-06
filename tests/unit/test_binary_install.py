import hashlib
import io
import os
import stat
import sys
import tarfile
import zipfile
from pathlib import Path

import httpx
import pytest

from strixrun.binaries.manager import BinaryError, BinaryManager, Manifest, safe_extract
from strixrun.events import EventBus
from strixrun.settings import SettingsStore

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell-script fake binary")
URL = "https://github.com/ggml-org/llama.cpp/releases/download/{tag}/{name}"
FAKE_SERVER = b'#!/bin/sh\necho "version: 9999 (fake)" 1>&2\n'


# ---- archive builders -------------------------------------------------------------------


def make_tar(members: dict[str, bytes], symlinks: dict[str, str] | None = None, mode=0o755) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            tf.addfile(info, io.BytesIO(data))
        for name, target in (symlinks or {}).items():
            info = tarfile.TarInfo(name)
            info.type, info.linkname = tarfile.SYMTYPE, target
            tf.addfile(info)
    return buf.getvalue()


def make_zip(members: dict[str, bytes], mode=0o755) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFREG | mode) << 16
            zf.writestr(info, data)
    return buf.getvalue()


# ---- safe extraction --------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["../evil", "/abs/evil", "a/../../evil", "C:/evil", "..\\evil"])
def test_zip_slip_rejected(tmp_path, bad):
    archive = tmp_path / "a.zip"
    archive.write_bytes(make_zip({bad: b"x"}))
    with pytest.raises(BinaryError, match="unsafe"):
        safe_extract(archive, "zip", tmp_path / "out")
    assert not (tmp_path / "evil").exists()


@posix_only
def test_zip_exec_bits_restored(tmp_path):
    archive = tmp_path / "a.zip"
    archive.write_bytes(make_zip({"bin/llama-server": FAKE_SERVER, "README": b"r"}))
    out = tmp_path / "out"
    safe_extract(archive, "zip", out)
    assert os.access(out / "bin" / "llama-server", os.X_OK)


def test_tar_traversal_rejected(tmp_path):
    archive = tmp_path / "a.tgz"
    archive.write_bytes(make_tar({"../evil": b"x"}))
    with pytest.raises(BinaryError, match="unsafe"):
        safe_extract(archive, "tar.gz", tmp_path / "out")


def test_tar_symlink_escaping_rejected(tmp_path):
    archive = tmp_path / "a.tgz"
    archive.write_bytes(make_tar({"d/f": b"x"}, symlinks={"d/link": "../../../etc/passwd"}))
    with pytest.raises(BinaryError, match="unsafe"):
        safe_extract(archive, "tar.gz", tmp_path / "out")


@posix_only
def test_tar_internal_symlinks_preserved(tmp_path):
    archive = tmp_path / "a.tgz"
    archive.write_bytes(make_tar({"d/libx.1.0.dylib": b"x"}, symlinks={"d/libx.dylib": "libx.1.0.dylib"}))
    out = tmp_path / "out"
    safe_extract(archive, "tar.gz", out)
    assert (out / "d" / "libx.dylib").is_symlink()
    assert (out / "d" / "libx.dylib").read_bytes() == b"x"


# ---- install / rollback -----------------------------------------------------------------


def manifest_for(builds: dict[str, bytes]) -> tuple[Manifest, dict[str, bytes]]:
    """builds: tag -> archive bytes for variant 'macos-arm64'."""
    served, tags = {}, {}
    for tag, blob in builds.items():
        name = f"llama-{tag}-bin-macos-arm64.tar.gz"
        url = URL.format(tag=tag, name=name)
        served[url] = blob
        tags[tag] = {"macos-arm64": {"asset": name, "url": url, "format": "tar.gz", "size": len(blob),
                                     "sha256": hashlib.sha256(blob).hexdigest(), "extras": []}}
    return Manifest({"default": next(iter(builds)), "tags": tags}), served


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STRIX_HOME", str(tmp_path / "strix"))
    store = SettingsStore()
    store.save(store.load())
    return store


def manager(store, manifest, served, bus=None):
    def handler(request: httpx.Request):
        body = served.get(str(request.url))
        return httpx.Response(200, content=body) if body is not None else httpx.Response(404)

    return BinaryManager(
        bus=bus or EventBus(), settings=store, manifest=manifest,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        variant=lambda: "macos-arm64",
    )


def build(tag: str, server: bytes = FAKE_SERVER) -> bytes:
    return make_tar({f"llama-{tag}/llama-server": server, f"llama-{tag}/LICENSE": b"MIT"})


@posix_only
async def test_install_happy_path_events_and_command(home):
    m, served = manifest_for({"b1": build("b1")})
    bus = EventBus()
    sub = bus.subscribe({"binary.progress"})
    bm = manager(home, m, served, bus)
    assert bm.command() is None
    status = await bm.install()
    assert status["current_tag"] == "b1"
    cmd = bm.command()
    assert cmd and cmd[0].endswith("llama-server") and os.access(cmd[0], os.X_OK)
    assert home.current.binary_tag == "b1" and home.current.binary_variant == "macos-arm64"
    phases = []
    while not sub.empty():
        phases.append((await sub.get()).data["phase"])
    assert phases[0] == "download" and phases[-1] == "done" and "verify" in phases


@posix_only
async def test_checksum_mismatch_leaves_current_untouched(home):
    m, served = manifest_for({"b1": build("b1"), "b2": build("b2")})
    bm = manager(home, m, served)
    await bm.install("b1")
    served[next(u for u in served if "b2" in u)] = b"tampered"
    with pytest.raises(BinaryError, match="checksum"):
        await bm.install("b2")
    assert home.current.binary_tag == "b1"
    assert bm.command() is not None
    assert not (bm.bin_dir / "b2").exists()


@posix_only
async def test_failed_smoke_test_leaves_current_untouched(home):
    m, served = manifest_for({"b1": build("b1"), "b2": build("b2", server=b"#!/bin/sh\nexit 3\n")})
    bm = manager(home, m, served)
    await bm.install("b1")
    with pytest.raises(BinaryError, match="did not run"):
        await bm.install("b2")
    assert home.current.binary_tag == "b1"


async def test_unknown_tag_rejected(home):
    m, served = manifest_for({"b1": build("b1")})
    with pytest.raises(BinaryError, match="not in the manifest"):
        await manager(home, m, served).install("b999")


@posix_only
async def test_rollback(home):
    m, served = manifest_for({"b1": build("b1"), "b2": build("b2")})
    bm = manager(home, m, served)
    await bm.install("b1")
    await bm.install("b2")
    assert bm.status()["previous_tag"] == "b1"
    await bm.rollback()
    assert home.current.binary_tag == "b1"
    assert "b1" in bm.command()[0]
    await bm.rollback()  # rolling back again undoes the rollback
    assert home.current.binary_tag == "b2"


async def test_rollback_without_history(home):
    m, served = manifest_for({"b1": build("b1")})
    with pytest.raises(BinaryError, match="nothing to roll back"):
        await manager(home, m, served).rollback()


def test_binary_path_override_wins(home, tmp_path):
    exe = tmp_path / "my-llama-server"
    exe.write_text("x")
    s = home.current
    s.binary_path = str(exe)
    home.save(s)
    m, served = manifest_for({"b1": build("b1")})
    assert manager(home, m, served).command() == [str(exe)]


@posix_only
async def test_cuda_runtime_lands_next_to_llama_server(home):
    # Linux CUDA releases ship the runtime in its own top-level folder; llama-server only
    # finds it beside itself ($ORIGIN rpath).
    m, served = manifest_for({"b1": build("b1")})
    runtime = make_tar({"cudart-llama-b1-bin-ubuntu-cuda-12.8-x64/libcudart.so.12": b"rt",
                        "cudart-llama-b1-bin-ubuntu-cuda-12.8-x64/libcublas.so.12": b"blas"})
    name = "cudart-llama-b1-bin-ubuntu-cuda-12.8-x64.tar.gz"
    url = URL.format(tag="b1", name=name)
    served[url] = runtime
    data = m._data["tags"]["b1"]["macos-arm64"]
    data["extras"] = [{"asset": name, "url": url, "format": "tar.gz", "size": len(runtime),
                       "sha256": hashlib.sha256(runtime).hexdigest()}]
    bm = manager(home, m, served)
    await bm.install()
    server_dir = Path(bm.command()[0]).parent
    assert (server_dir / "libcudart.so.12").read_bytes() == b"rt"
    assert (server_dir / "libcublas.so.12").read_bytes() == b"blas"
    assert not any(p.name.startswith("cudart-llama") for p in bm.bin_dir.rglob("*"))
