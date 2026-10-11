import os
import stat
import sys
from pathlib import Path

import httpx
import pytest

from enjrorun.app import create_app
from enjrorun.settings import SettingsStore

from .conftest import serve_app

FAKE = str(Path(__file__).resolve().parents[1] / "fake_llama_server.py")
TOKEN = "hf_" + "a1B2" * 9


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ENJRO_HOME", str(tmp_path / "strix"))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr("huggingface_hub.constants.HF_TOKEN_PATH", str(tmp_path / "hf" / "token"))
    return tmp_path


def app():
    return create_app(binary=lambda: [sys.executable, FAKE])


async def test_get_settings_shape(home):
    async with serve_app(app()) as url, httpx.AsyncClient(base_url=url) as c:
        s = (await c.get("/admin/settings")).json()
    assert "api_key_hash" not in s and s["api_key_set"] is False
    assert {"host", "binary_path", "models_dirs", "extra_scan_dirs"} <= set(s["file_only"])
    assert s["hf_token"] == {"set": False, "source": None}


@pytest.mark.parametrize("field,value", [("binary_path", "/bin/sh"), ("host", "0.0.0.0"),
                                         ("models_dirs", ["/"]), ("extra_scan_dirs", ["/"])])
async def test_put_file_only_fields_rejected(home, field, value):
    async with serve_app(app()) as url, httpx.AsyncClient(base_url=url) as c:
        r = await c.put("/admin/settings", json={field: value})
    assert r.status_code == 400 and "file-only" in r.json()["error"]["message"]
    assert getattr(SettingsStore().load(), field) != value


async def test_put_valid_and_invalid(home):
    async with serve_app(app()) as url, httpx.AsyncClient(base_url=url) as c:
        r = await c.put("/admin/settings", json={"log_retention_days": 7, "restore_loaded": False})
        assert r.status_code == 200 and r.json()["log_retention_days"] == 7
        assert (await c.put("/admin/settings", json={"log_retention_days": 0})).status_code == 400
        assert (await c.put("/admin/settings", json={"api_key_hash": "x"})).status_code == 400
        assert (await c.put("/admin/settings", json={"nope": 1})).status_code == 400
    assert SettingsStore().load().log_retention_days == 7


async def test_api_key_lifecycle_and_rotation_invalidates_sessions(home):
    async with serve_app(app()) as url:
        async with httpx.AsyncClient(base_url=url) as c:
            key1 = (await c.post("/admin/settings/api-key", json={"action": "generate"})).json()["key"]
            assert key1.startswith("sk-enjro-")
            assert key1 not in (await c.get("/admin/settings")).text  # shown once only
            await c.put("/admin/settings", json={"trust_loopback": False}, headers={"Authorization": f"Bearer {key1}"})

        browser_a, browser_b = httpx.AsyncClient(base_url=url), httpx.AsyncClient(base_url=url)
        async with browser_a, browser_b:
            for b in (browser_a, browser_b):
                assert (await b.get("/admin/ps")).status_code == 401
                assert (await b.post("/admin/session", json={"key": key1})).status_code == 200
                assert (await b.get("/admin/ps")).status_code == 200
            # browser A rotates the key: A keeps working, B's old session is dead
            r = await browser_a.post("/admin/settings/api-key", json={"action": "rotate"})
            key2 = r.json()["key"]
            assert key2 != key1
            assert (await browser_a.get("/admin/ps")).status_code == 200
            assert (await browser_b.get("/admin/ps")).status_code == 401
            async with httpx.AsyncClient(base_url=url) as c:
                assert (await c.get("/admin/ps", headers={"Authorization": f"Bearer {key1}"})).status_code == 401
                assert (await c.get("/admin/ps", headers={"Authorization": f"Bearer {key2}"})).status_code == 200


async def test_hf_token_set_masked_and_unset(home):
    async with serve_app(app()) as url, httpx.AsyncClient(base_url=url) as c:
        r = await c.put("/admin/settings/hf-token", json={"token": TOKEN})
        assert r.json()["hf_token"] == {"set": True, "source": "store"}
        assert TOKEN not in (await c.get("/admin/settings")).text
        path = home / "hf" / "token"
        assert path.read_text() == TOKEN
        if os.name != "nt":
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert (await c.put("/admin/settings/hf-token", json={"token": "not-a-token"})).status_code == 400
        r = await c.put("/admin/settings/hf-token", json={"token": None})
        assert r.json()["hf_token"]["set"] is False and not path.exists()


async def test_hf_token_env_reported(home, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", TOKEN)
    async with serve_app(app()) as url, httpx.AsyncClient(base_url=url) as c:
        assert (await c.get("/admin/settings")).json()["hf_token"] == {"set": True, "source": "env"}
