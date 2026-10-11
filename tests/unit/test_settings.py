import json
import os
import stat
import sys
from pathlib import Path

import pytest

from enjrorun import paths
from enjrorun.settings import (
    FILE_ONLY_FIELDS,
    Settings,
    SettingsError,
    SettingsStore,
    generate_api_key,
    hash_api_key,
    normalize_public_host,
    verify_api_key,
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ENJRO_HOME", str(tmp_path / "strix"))
    return tmp_path / "strix"


def test_enjro_home_env_override(home):
    assert paths.enjro_home() == home
    assert paths.run_dir() == home / "run"
    assert paths.config_path() == home / "run" / "config.json"


def test_enjro_home_default(monkeypatch, tmp_path):
    monkeypatch.delenv("ENJRO_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert paths.enjro_home() == tmp_path / ".enjro"


def test_defaults_when_no_file(home):
    s = SettingsStore().load()
    assert s.host == "127.0.0.1"
    assert s.port == 8790
    assert s.api_key_hash is None
    assert s.trust_loopback is True
    assert s.restore_loaded is True
    assert s.log_body_ring_size == 50
    assert s.log_retention_days == 30
    assert s.upstream_idle_timeout_s == 120
    assert s.binary_path is None
    assert s.models_dirs == [str(home / "run" / "models")]
    assert s.extra_scan_dirs == []


def test_save_roundtrip_and_atomic(home):
    store = SettingsStore()
    s = store.load()
    s.port = 9000
    store.save(s)
    assert json.loads(paths.config_path().read_text())["port"] == 9000
    assert SettingsStore().load().port == 9000
    assert not list(paths.run_dir().glob("*.tmp"))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_save_mode_0600(home):
    store = SettingsStore()
    store.save(store.load())
    mode = stat.S_IMODE(os.stat(paths.config_path()).st_mode)
    assert mode == 0o600


def test_unknown_keys_in_file_are_ignored(home):
    paths.run_dir().mkdir(parents=True)
    paths.config_path().write_text(json.dumps({"port": 1234, "bogus": 1}))
    assert SettingsStore().load().port == 1234


def test_api_key_hash_and_verify():
    key = generate_api_key()
    assert key.startswith("sk-enjro-") and len(key) > 40
    h = hash_api_key(key)
    assert key not in h
    assert verify_api_key(key, h)
    assert not verify_api_key(key + "x", h)
    assert not verify_api_key(key, None)
    assert hash_api_key(key) != h  # salted


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_loopback_host_without_key_is_valid(host):
    Settings(host=host).validate()


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "::"])
def test_lan_host_without_key_rejected(host):
    with pytest.raises(SettingsError, match="enjrorun key set"):
        Settings(host=host).validate()


def test_lan_host_with_key_is_valid():
    Settings(host="0.0.0.0", api_key_hash=hash_api_key(generate_api_key())).validate()


def test_public_hosts_without_key_rejected():
    with pytest.raises(SettingsError, match="enjrorun key set"):
        Settings(public_hosts=["enjro.example.com"]).validate()
    Settings(public_hosts=["enjro.example.com"],
             api_key_hash=hash_api_key(generate_api_key())).validate()


@pytest.mark.parametrize("raw", ["enjro.example.com", "https://Enjro.Example.com/", " ENJRO.example.com/v1 "])
def test_normalize_public_host(raw):
    assert normalize_public_host(raw) == "enjro.example.com"


def test_api_update_rejects_file_only_fields(home):
    store = SettingsStore()
    store.save(store.load())
    assert {"host", "public_hosts", "binary_path", "models_dirs", "extra_scan_dirs"} <= FILE_ONLY_FIELDS
    for field in FILE_ONLY_FIELDS:
        with pytest.raises(SettingsError, match="file-only"):
            store.update_from_api({field: "x"})


def test_api_update_rejects_unknown_field(home):
    with pytest.raises(SettingsError, match="unknown"):
        SettingsStore().update_from_api({"nope": 1})


def test_api_update_applies_and_notifies(home):
    store = SettingsStore()
    seen = []
    store.on_change(seen.append)
    s = store.update_from_api({"log_retention_days": 7, "restore_loaded": False})
    assert s.log_retention_days == 7 and s.restore_loaded is False
    assert SettingsStore().load().log_retention_days == 7
    assert seen and seen[-1].log_retention_days == 7


def test_clearing_key_while_lan_host_rejected(home):
    store = SettingsStore()
    s = store.load()
    s.host = "0.0.0.0"
    s.api_key_hash = hash_api_key(generate_api_key())
    store.save(s)
    with pytest.raises(SettingsError):
        store.set_api_key_hash(None)


def test_public_dict_hides_key_hash(home):
    s = Settings(api_key_hash=hash_api_key(generate_api_key()))
    d = s.public_dict()
    assert "api_key_hash" not in d
    assert d["api_key_set"] is True
    assert set(d["file_only"]) == FILE_ONLY_FIELDS
