import sys
from pathlib import Path

import httpx
import pytest

from strixrun.app import create_app

from .conftest import serve_app

FAKE = str(Path(__file__).resolve().parents[1] / "fake_llama_server.py")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STRIX_HOME", str(tmp_path / "strix"))


async def test_binary_status_and_validation(home):
    async with serve_app(create_app(binary=lambda: [sys.executable, FAKE])) as url, \
            httpx.AsyncClient(base_url=url) as c:
        status = (await c.get("/admin/binary")).json()
        assert status["default_tag"] in status["available_tags"]
        assert status["installing"] is None
        r = await c.post("/admin/binary/install", json={"tag": "b0-not-pinned"})
        assert r.status_code == 400 and "manifest" in r.json()["error"]["message"]
        r = await c.post("/admin/binary/rollback", json={})
        assert r.status_code == 409
