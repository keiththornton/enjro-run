import asyncio
import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from strixrun import paths
from strixrun.app import create_app
from strixrun.cli import app
from strixrun.registry import ModelEntry, Registry
from strixrun.settings import SettingsStore, verify_api_key

from .conftest import serve_app

runner = CliRunner()


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STRIX_HOME", str(tmp_path / "strix"))
    return tmp_path


def test_key_set_prints_key_once_and_stores_hash():
    r = runner.invoke(app, ["key", "set"])
    assert r.exit_code == 0
    key = r.stdout.strip()
    assert key.startswith("sk-strix-")
    stored = json.loads(paths.config_path().read_text())["api_key_hash"]
    assert key not in stored and verify_api_key(key, stored)


def test_lan_host_requires_key_first():
    r = runner.invoke(app, ["config", "set", "host", "0.0.0.0"])
    assert r.exit_code == 1 and "strixrun key set" in r.output
    assert runner.invoke(app, ["key", "set"]).exit_code == 0
    assert runner.invoke(app, ["config", "set", "host", "0.0.0.0"]).exit_code == 0
    assert SettingsStore().load().host == "0.0.0.0"
    # and the key can't be cleared while exposed
    assert runner.invoke(app, ["key", "clear"]).exit_code == 1


def test_config_set_types_and_paths(tmp_path):
    assert runner.invoke(app, ["config", "set", "restore_loaded", "false"]).exit_code == 0
    assert runner.invoke(app, ["config", "set", "port", "9999"]).exit_code == 0
    d1, d2 = tmp_path / "a", tmp_path / "b"
    assert runner.invoke(app, ["config", "set", "extra_scan_dirs", f"{d1},{d2}"]).exit_code == 0
    s = SettingsStore().load()
    assert s.restore_loaded is False and s.port == 9999
    assert s.extra_scan_dirs == [str(d1.resolve()), str(d2.resolve())]
    assert runner.invoke(app, ["config", "set", "port", "abc"]).exit_code == 1
    assert runner.invoke(app, ["config", "set", "binary_path", str(tmp_path / "missing")]).exit_code == 1
    assert runner.invoke(app, ["config", "set", "api_key_hash", "x"]).exit_code == 1
    assert runner.invoke(app, ["config", "set", "nope", "x"]).exit_code == 1


def test_config_show_hides_hash():
    runner.invoke(app, ["key", "set"])
    r = runner.invoke(app, ["config", "show"])
    assert r.exit_code == 0 and "api_key_hash" not in r.stdout and '"api_key_set": true' in r.stdout


# ---- commands against a running server -------------------------------------------------




FAKE = str(Path(__file__).resolve().parents[1] / "fake_llama_server.py")


async def cli(*args):
    return await asyncio.to_thread(runner.invoke, app, list(args))


@pytest.fixture
def seeded():
    Registry(paths.registry_path()).add(
        ModelEntry(id="test--chat--q4", aliases=["chat:q4"], paths=["/m.gguf"], size_bytes=1))


async def test_load_ps_unload(seeded, monkeypatch):
    async with serve_app(create_app(binary=lambda: [sys.executable, FAKE], load_timeout=20)) as url:
        monkeypatch.setenv("STRIXRUN_URL", url)
        r = await cli("load", "chat:q4", "--ctx", "2048")
        assert r.exit_code == 0, r.output
        assert "chat:q4 is ready" in r.output
        r = await cli("ps")
        assert "chat:q4" in r.output and "2048" in r.output and "ready" in r.output
        r = await cli("unload", "chat:q4")
        assert r.exit_code == 0 and "Unloaded chat:q4" in r.output
        assert "No models loaded" in (await cli("ps")).output


async def test_load_crash_exits_nonzero_with_reason(seeded, monkeypatch):
    monkeypatch.setenv("FAKE_STARTUP_ERROR", "oom")
    async with serve_app(create_app(binary=lambda: [sys.executable, FAKE], load_timeout=20)) as url:
        monkeypatch.setenv("STRIXRUN_URL", url)
        r = await cli("load", "chat:q4")
    assert r.exit_code == 1
    assert "out_of_memory" in r.output and "not enough memory" in r.output


async def test_unknown_model_and_server_down(seeded, monkeypatch):
    async with serve_app(create_app(binary=lambda: [sys.executable, FAKE])) as url:
        monkeypatch.setenv("STRIXRUN_URL", url)
        r = await cli("load", "nope")
        assert r.exit_code == 1 and "no model named 'nope'" in r.output and "chat:q4" in r.output
    monkeypatch.setenv("STRIXRUN_URL", "http://127.0.0.1:1")
    r = await cli("ps")
    assert r.exit_code == 1 and "strixrun serve" in r.output


async def test_needs_key(seeded, monkeypatch):
    runner.invoke(app, ["key", "set"])
    runner.invoke(app, ["config", "set", "trust_loopback", "false"])
    async with serve_app(create_app(binary=lambda: [sys.executable, FAKE])) as url:
        monkeypatch.setenv("STRIXRUN_URL", url)
        r = await cli("ps")
    assert r.exit_code == 1 and "STRIXRUN_API_KEY" in r.output


def test_serve_refuses_busy_port():
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        r = runner.invoke(app, ["serve", "--port", str(port)])
    assert r.exit_code == 1 and f"port {port} is already in use" in r.output


def test_serve_refuses_when_already_running_on_this_folder():
    from strixrun.instance import InstanceLock

    lock = InstanceLock(paths.run_dir() / "server.lock")
    lock.acquire()
    try:
        r = runner.invoke(app, ["serve", "--port", "1"])
    finally:
        lock.release()
    assert r.exit_code == 1 and "already running" in r.output and str(__import__("os").getpid()) in r.output


# ---- open / stop -------------------------------------------------------------------------


async def test_shutdown_endpoint_is_loopback_only_and_stops_server():
    import httpx

    key = runner.invoke(app, ["key", "set"]).stdout.strip()
    a = create_app(binary=lambda: [sys.executable, FAKE])
    stopped = []
    a.state.request_shutdown = lambda: stopped.append(True)
    async with serve_app(a) as url:
        transport = httpx.ASGITransport(app=a, client=("192.168.1.20", 5000))
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8790") as lan:
            r = await lan.post("/admin/shutdown", json={}, headers={"Authorization": f"Bearer {key}"})
            assert r.status_code == 403 and stopped == []
        async with httpx.AsyncClient(base_url=url) as local:
            r = await local.post("/admin/shutdown", json={})
            assert r.status_code == 202 and stopped == [True]


def test_open_starts_server_and_stop_stops_it(tmp_path, monkeypatch):
    import socket
    import time

    import httpx

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    runner.invoke(app, ["config", "set", "port", str(port)])
    try:
        r = runner.invoke(app, ["open", "--no-browser"])
        assert r.exit_code == 0, r.output
        assert f"http://127.0.0.1:{port}" in r.output
        assert httpx.get(f"http://127.0.0.1:{port}/v1/models").status_code == 200
        r = runner.invoke(app, ["open", "--no-browser"])  # already running: no second server
        assert r.exit_code == 0 and "already running" in r.output
    finally:
        r = runner.invoke(app, ["stop"])
    assert r.exit_code == 0 and "Stopped" in r.output
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            httpx.get(f"http://127.0.0.1:{port}/v1/models", timeout=0.5)
        except httpx.TransportError:
            break
        time.sleep(0.2)
    else:
        raise AssertionError("server still answering after stop")
    assert "isn't running" in runner.invoke(app, ["stop"]).output


def test_stop_falls_back_to_ending_the_process(monkeypatch):
    """If the server ignores the shutdown request, stop ends it via the PID in the lock file."""
    import socket
    import time

    import httpx

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    runner.invoke(app, ["config", "set", "port", str(port)])
    assert runner.invoke(app, ["open", "--no-browser"]).exit_code == 0
    try:
        real_post = httpx.post
        monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Response(202, json={"stopping": True},
                                                                            request=httpx.Request("POST", url)))
        r = runner.invoke(app, ["stop", "--timeout", "2"])
        assert r.exit_code == 0, r.output
        assert "Stopped StrixRun" in r.output and "ended" in r.output
        with pytest.raises(httpx.TransportError):
            httpx.get(f"http://127.0.0.1:{port}/v1/models", timeout=1)
    finally:
        monkeypatch.setattr(httpx, "post", real_post)
        runner.invoke(app, ["stop"])
        time.sleep(0.5)
