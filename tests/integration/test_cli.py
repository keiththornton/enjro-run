import json

import pytest
from typer.testing import CliRunner

from strixrun import paths
from strixrun.cli import app
from strixrun.settings import SettingsStore, verify_api_key

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
