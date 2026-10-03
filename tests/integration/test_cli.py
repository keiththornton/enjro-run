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
