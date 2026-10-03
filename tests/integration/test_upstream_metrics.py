import sys
from pathlib import Path

import httpx
import pytest

from strixrun import paths
from strixrun.app import create_app
from strixrun.registry import ModelEntry, Registry

from .conftest import serve_app

FAKE = str(Path(__file__).resolve().parents[1] / "fake_llama_server.py")
MID, ALIAS = "test--chat--q4", "chat:q4"


@pytest.fixture
async def running(tmp_path, monkeypatch):
    monkeypatch.setenv("STRIX_HOME", str(tmp_path / "strix"))
    Registry(paths.registry_path()).add(ModelEntry(id=MID, aliases=[ALIAS], paths=["/m.gguf"], size_bytes=1))
    a = create_app(binary=lambda: [sys.executable, FAKE], load_timeout=20)
    async with serve_app(a) as url, httpx.AsyncClient(base_url=url, timeout=30) as c:
        await c.post(f"/admin/models/{MID}/load", json={})
        await a.state.ctx.backend.wait_settled(MID)
        yield a, c


async def test_upstream_allowlisted_routes_reach_child_and_are_logged(running):
    a, c = running
    seen = []
    a.state.ctx.bus.on("request.completed", lambda ev: seen.append(ev.data))
    r = await c.get(f"/upstream/{ALIAS}/props")
    assert r.status_code == 200 and r.json()["total_slots"] == 2
    r = await c.post(f"/upstream/{ALIAS}/tokenize", json={"content": "a b c"})
    assert r.json()["tokens"] == [0, 1, 2]
    assert [rec.endpoint for rec in seen] == ["upstream", "upstream"]
    assert seen[0].alias_used == ALIAS and seen[0].http_status == 200


@pytest.mark.parametrize(
    "method,path",
    [("POST", "slots/0?action=save"), ("POST", "lora-adapters"), ("GET", "metrics"),
     ("POST", "completion"), ("GET", "../admin/ps"), ("POST", "props")],
)
async def test_upstream_blocks_everything_else(running, method, path):
    _, c = running
    r = await c.request(method, f"/upstream/{ALIAS}/{path}", json={})
    assert r.status_code == 404


async def test_upstream_unknown_model(running):
    _, c = running
    assert (await c.get("/upstream/nope/health")).status_code == 404


async def test_metrics_after_two_requests(running):
    _, c = running
    for _ in range(2):  # streaming: TTFT is only measured on streams
        async with c.stream("POST", "/v1/chat/completions",
                            json={"model": ALIAS, "messages": [], "stream": True}) as r:
            await r.aread()
    text = (await c.get("/metrics")).text
    assert f'strixrun_requests_total{{model="{MID}",endpoint="chat",status="ok"}} 2' in text
    assert f'strixrun_ttft_seconds_count{{model="{MID}"}} 2' in text
    assert f'strixrun_ttft_seconds_bucket{{model="{MID}",le="+Inf"}} 2' in text
    assert f'strixrun_queue_depth{{model="{MID}"}} 0' in text
    assert f'strixrun_model_loaded{{model="{MID}"}} 1' in text
    assert "strixrun_completion_tokens_total" in text
