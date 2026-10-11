import json
import os
import sys

import pytest

from enjrorun import migrate, paths
from enjrorun.instance import InstanceLock
from enjrorun.settings import Settings, SettingsStore


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.delenv("ENJRO_HOME", raising=False)
    monkeypatch.delenv("STRIX_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


def make_legacy(home):
    legacy = home / ".strix" / "run"
    (legacy / "models" / "owner--repo").mkdir(parents=True)
    (legacy / "models" / "owner--repo" / "m.gguf").write_bytes(b"GGUF")
    (legacy / "presets").mkdir()
    models_dir = str(legacy / "models")
    config = {"host": "127.0.0.1", "models_dirs": [models_dir], "extra_scan_dirs": ["/elsewhere/models"],
              "binary_path": str(legacy / "bin" / "llama-server")}
    (legacy / "config.json").write_text(json.dumps(config))
    os.chmod(legacy / "config.json", 0o600)
    (legacy / "models.json").write_text(json.dumps(
        {"models": [{"id": "a", "path": str(legacy / "models" / "owner--repo" / "m.gguf")}]}))
    (legacy / "presets" / "a.json").write_text(json.dumps({"ctx": 4096}))
    (legacy / paths.LEGACY_DB_NAME).write_bytes(b"sqlite")
    return legacy


def test_fresh_install_uses_enjro_folder(home):
    assert paths.run_dir() == home / ".enjro" / "run"
    assert paths.db_path() == home / ".enjro" / "run" / "enjrorun.db"


def test_unmigrated_legacy_folder_keeps_being_used(home):
    legacy = make_legacy(home)
    assert paths.run_dir() == legacy
    assert paths.db_path() == legacy / paths.LEGACY_DB_NAME
    assert SettingsStore().load().models_dirs == [str(legacy / "models")]


def test_migrate_moves_folder_and_rewrites_paths(home):
    legacy = make_legacy(home)
    message = migrate.migrate_data()
    new = home / ".enjro" / "run"
    assert message and str(new) in message
    assert not legacy.exists() and not (home / ".strix").exists()  # empty parent removed
    assert (new / "models" / "owner--repo" / "m.gguf").read_bytes() == b"GGUF"
    assert paths.run_dir() == new

    config = json.loads((new / "config.json").read_text())
    assert config["models_dirs"] == [str(new / "models")]
    assert config["binary_path"] == str(new / "bin" / "llama-server")
    assert config["extra_scan_dirs"] == ["/elsewhere/models"]  # outside the folder: untouched
    if os.name != "nt":
        assert (new / "config.json").stat().st_mode & 0o777 == 0o600
    registry = json.loads((new / "models.json").read_text())
    assert registry["models"][0]["path"] == str(new / "models" / "owner--repo" / "m.gguf")

    assert (new / "enjrorun.db").read_bytes() == b"sqlite"
    assert paths.db_path() == new / "enjrorun.db"
    assert migrate.migrate_data() is None  # idempotent


def test_migrate_keeps_shared_strix_folder(home):
    make_legacy(home)
    (home / ".strix" / "code").mkdir()  # a sister app's data
    migrate.migrate_data()
    assert (home / ".strix" / "code").is_dir()


def test_migrate_blocked_while_old_server_runs(home):
    legacy = make_legacy(home)
    lock = InstanceLock(legacy / "server.lock")
    lock.acquire()
    try:
        with pytest.raises(migrate.MigrationBlocked, match="still running"):
            migrate.migrate_data()
    finally:
        lock.release()
    assert legacy.is_dir() and not (home / ".enjro").exists()
    assert paths.run_dir() == legacy


def test_migrate_never_overwrites_existing_enjro_folder(home):
    legacy = make_legacy(home)
    (home / ".enjro" / "run").mkdir(parents=True)
    assert migrate.migrate_data() is None
    assert legacy.is_dir()


@pytest.mark.parametrize("var", ["ENJRO_HOME", "STRIX_HOME"])
def test_custom_home_is_used_in_place(home, monkeypatch, var):
    custom = home / "data"
    monkeypatch.setenv(var, str(custom))
    make_legacy(home)
    assert paths.run_dir() == custom / "run"
    assert migrate.migrate_data() is None


def test_env_falls_back_to_strixrun_names(monkeypatch):
    monkeypatch.delenv("ENJRORUN_API_KEY", raising=False)
    monkeypatch.setenv("STRIXRUN_API_KEY", "old")
    assert paths.env("API_KEY") == "old"
    monkeypatch.setenv("ENJRORUN_API_KEY", "new")
    assert paths.env("API_KEY") == "new"


@pytest.mark.skipif(sys.platform != "linux", reason="systemd path")
def test_legacy_systemd_unit_replaced(home, monkeypatch):
    unit = home / ".config" / "systemd" / "user" / migrate.LEGACY_UNIT_NAME
    unit.parent.mkdir(parents=True)
    unit.write_text("[Service]\nExecStart=strixrun serve\n")
    calls = []
    monkeypatch.setattr(migrate, "_run", lambda cmd: calls.append(cmd))
    monkeypatch.setattr(migrate.service, "install", lambda: "installed")
    assert migrate.legacy_service_present()
    assert migrate.migrate_service() == "Replaced the StrixRun login service. installed"
    assert not unit.exists() and not migrate.legacy_service_present()
    assert ["systemctl", "--user", "disable", "--now", migrate.LEGACY_UNIT_NAME] in calls
    assert migrate.migrate_service() is None


def test_no_legacy_service_installs_nothing(home, monkeypatch):
    monkeypatch.setattr(migrate, "_platform", lambda: "linux")
    monkeypatch.setattr(migrate.service, "install", lambda: pytest.fail("must not install"))
    assert migrate.migrate_all() == []


def test_settings_default_models_dir_follows_folder(home):
    assert Settings().models_dirs == [str(home / ".enjro" / "run" / "models")]
