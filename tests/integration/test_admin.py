import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

from strixrun import paths
from strixrun.app import create_app
from strixrun.presets import Preset, PresetStore
from strixrun.registry import ModelEntry, Registry
from strixrun.settings import SettingsStore

from .conftest import serve_app

FAKE = str(Path(__file__).resolve().parents[1] / "fake_llama_server.py")
MID, ALIAS = "test--chat--q4", "chat:q4"
JSON = {"Content-Type": "application/json"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STRIX_HOME", str(tmp_path / "strix"))
    Registry(paths.registry_path()).add(
        ModelEntry(id=MID, aliases=[ALIAS], paths=["/m/chat.gguf"], size_bytes=1)
    )
    return tmp_path


def app():
    return create_app(binary=lambda: [sys.executable, FAKE], load_timeout=20)


async def sse_until(client, url, predicate, timeout=15.0):
    """Collect SSE events until predicate(events) is true."""
    events = []

    async def run():
        async with client.stream("GET", url) as r:
            assert r.status_code == 200
            event = None
            async for line in r.aiter_lines():
                if line.startswith("event: "):
                    event = line[7:]
                elif line.startswith("data: "):
                    events.append((event, json.loads(line[6:])))
                    if predicate(events):
                        return

    await asyncio.wait_for(run(), timeout)
    return events


async def test_load_events_request_feed_and_unload(home):
    async with serve_app(app()) as url, httpx.AsyncClient(base_url=url, timeout=30) as c:
        def ready(evs):
            return any(e == "model.state" and d["status"] == "ready" for e, d in evs)

        watcher = asyncio.create_task(sse_until(c, "/admin/models/events", ready))
        await asyncio.sleep(0.3)
        r = await c.post(f"/admin/models/{MID}/load", json={"preset": {"ctx_per_request": 2048}})
        assert r.status_code == 202 and r.json()["state"]["status"] == "loading"
        events = await watcher
        statuses = [d["status"] for e, d in events if e == "model.state"]
        assert statuses[0] == "loading" and statuses[-1] == "ready"
        assert any(e == "model.log" for e, _ in events)

        # last-used preset is remembered
        assert PresetStore(paths.presets_dir()).load(MID, {}).ctx_per_request == 2048

        req_watch = asyncio.create_task(
            sse_until(c, "/admin/requests/events", lambda evs: len(evs) >= 1)
        )
        await asyncio.sleep(0.3)
        chat = await c.post("/v1/chat/completions", json={"model": ALIAS, "messages": []})
        assert chat.status_code == 200
        (_, row), = await req_watch
        assert row["model_id"] == MID and row["completion_tokens"] == 5
        assert "request_body" not in row

        detail = (await c.get(f"/admin/requests/{row['id']}")).json()
        assert detail["bodies_available"] and detail["request_body"]["model"] == ALIAS
        assert (await c.get("/admin/requests")).json()["data"][0]["id"] == row["id"]

        ps = (await c.get("/admin/ps")).json()["data"]
        assert ps[0]["id"] == MID and ps[0]["ctx_per_request"] == 2048 and ps[0]["rss_bytes"]
        assert any("listening" in line for line in (await c.get(f"/admin/models/{MID}/logs")).json()["lines"])

        again = await c.post(f"/admin/models/{MID}/load", json={})
        assert again.status_code == 409 and again.json()["error"]["code"] == "model_already_loaded"

        r = await c.post(f"/admin/models/{MID}/unload", json={})
        assert r.json()["state"]["status"] == "stopped"


async def test_invalid_preset_is_400(home):
    async with serve_app(app()) as url, httpx.AsyncClient(base_url=url) as c:
        r = await c.post(f"/admin/models/{MID}/load", json={"preset": {"extra_args": ["--log-file", "x"]}})
    assert r.status_code == 400 and "not allowed" in r.json()["error"]["message"]


async def test_alias_rename_resolves_on_v1(home):
    a = app()
    async with serve_app(a) as url, httpx.AsyncClient(base_url=url, timeout=30) as c:
        r = await c.patch(f"/admin/models/{MID}", json={"aliases": ["renamed", "gpt-4o-mini"]})
        assert r.status_code == 200 and r.json()["aliases"] == ["renamed", "gpt-4o-mini"]
        assert (await c.patch(f"/admin/models/{MID}", json={"aliases": ["bad alias"]})).status_code == 400
        await c.post(f"/admin/models/{MID}/load", json={})
        await a.state.ctx.backend.wait_settled(MID)
        r = await c.post("/v1/chat/completions", json={"model": "gpt-4o-mini", "messages": []})
        assert r.status_code == 200


async def test_delete_invariants(home):
    store_dir = Path(SettingsStore().load().models_dirs[0])
    repo_dir = store_dir / "owner--big"
    repo_dir.mkdir(parents=True)
    shards = [repo_dir / f"big-0000{i}-of-00002.gguf" for i in (1, 2)]
    for s in shards:
        s.write_bytes(b"x")
    reg = Registry(paths.registry_path())
    big = reg.add(ModelEntry.from_download("owner", "big-GGUF", "Q4_K_M", [str(s) for s in shards], 2))
    scan_dir = home / "elsewhere"
    scan_dir.mkdir()
    (scan_dir / "keep.gguf").write_bytes(b"x")
    scanned = reg.add(ModelEntry(id="local--keep", aliases=["keep"], paths=[str(scan_dir / "keep.gguf")],
                                 size_bytes=1, location="scan"))
    PresetStore(paths.presets_dir()).save(big.id, Preset(ctx_per_request=1024))

    a = app()
    async with serve_app(a) as url, httpx.AsyncClient(base_url=url, timeout=30) as c:
        # loaded models can't be deleted
        await c.post(f"/admin/models/{big.id}/load", json={})
        await a.state.ctx.backend.wait_settled(big.id)
        r = await c.delete(f"/admin/models/{big.id}")
        assert r.status_code == 409
        await c.post(f"/admin/models/{big.id}/unload", json={})

        # scan-folder models are never deleted
        r = await c.delete(f"/admin/models/{scanned.id}")
        assert r.status_code == 403 and (scan_dir / "keep.gguf").exists()

        r = await c.delete(f"/admin/models/{big.id}")
        assert r.status_code == 200
        assert not any(s.exists() for s in shards) and not repo_dir.exists()
        assert (await c.get(f"/admin/models/{big.id}")).status_code == 404
        assert not (paths.presets_dir() / f"{big.id}.json").exists()

        # re-downloading the same model gives the same id, and it loads
        repo_dir.mkdir()
        for s in shards:
            s.write_bytes(b"x")
        again = a.state.ctx.registry.add(
            ModelEntry.from_download("owner", "big-GGUF", "Q4_K_M", [str(s) for s in shards], 2)
        )
        assert again.id == big.id
        await c.post(f"/admin/models/{big.id}/load", json={})
        assert (await a.state.ctx.backend.wait_settled(big.id)).status.value == "ready"


async def test_delete_refuses_paths_outside_store(home):
    outside = home / "outside.gguf"
    outside.write_bytes(b"x")
    Registry(paths.registry_path()).add(
        ModelEntry(id="evil--x--q4", aliases=["evil"], paths=[str(outside)], size_bytes=1, location="store")
    )
    async with serve_app(app()) as url, httpx.AsyncClient(base_url=url) as c:
        r = await c.delete("/admin/models/evil--x--q4")
    assert r.status_code == 403 and outside.exists()


async def test_unknown_model_404(home):
    async with serve_app(app()) as url, httpx.AsyncClient(base_url=url) as c:
        assert (await c.post("/admin/models/nope/load", json={})).status_code == 404
        assert (await c.get("/admin/requests/nope")).status_code == 404


async def test_session_without_key_configured(home):
    async with serve_app(app()) as url, httpx.AsyncClient(base_url=url) as c:
        r = await c.post("/admin/session", json={"key": "anything"})
        assert r.json() == {"authenticated": True, "key_required": False}
        assert (await c.get("/admin/session")).json()["via"] == "loopback"


async def test_estimate_and_confirmation_gate(home):
    from strixrun.memory import MemoryEstimator
    from tests.unit.test_memory import FakeProbe

    a = app()
    async with serve_app(a) as url, httpx.AsyncClient(base_url=url, timeout=30) as c:
        # 1 GiB available: the fixed ~0.5 GiB compute overhead alone is > 85% → "tight"
        a.state.ctx.estimator = MemoryEstimator(FakeProbe(available=600 * 1024 * 1024))
        est = (await c.post(f"/admin/models/{MID}/estimate", json={"preset": {"ctx_per_request": 4096}})).json()
        assert est["verdict"] == "tight" and est["ctx_per_request"] == 4096
        r = await c.post(f"/admin/models/{MID}/load", json={})
        assert r.status_code == 409
        body = r.json()
        assert body["error"]["code"] == "needs_confirmation" and body["estimate"]["verdict"] == "tight"
        assert a.state.ctx.backend.loaded() == []
        r = await c.post(f"/admin/models/{MID}/load", json={"confirm": True})
        assert r.status_code == 202


async def test_hf_routes_with_fake_hub(home):
    from tests.unit.test_downloads import FILES, FakeHub

    a = app()
    async with serve_app(a) as url, httpx.AsyncClient(base_url=url, timeout=30) as c:
        a.state.ctx.downloads.hub = FakeHub(FILES)
        assert (await c.get("/admin/hf/search", params={"q": "model"})).json()["data"][0]["repo"]
        listing = (await c.get("/admin/hf/repo/owner/Model-GGUF/files")).json()
        assert {q["quant"] for q in listing["quants"]} == {"Q4_K_M", "Q8_0", "F16"}
        r = await c.post("/admin/downloads", json={"repo": "owner/Model-GGUF", "quant": "Q9_X"})
        assert r.status_code == 400


async def test_discover_route_with_fake_hub(home):
    from tests.unit.test_discover import ROWS
    from tests.unit.test_discover import FakeHub as DiscoverHub

    a = app()
    async with serve_app(a) as url, httpx.AsyncClient(base_url=url) as c:
        a.state.ctx.discover.hub = DiscoverHub(ROWS)
        r = await c.get("/admin/hf/discover", params={"sort": "downloads", "kind": "chat"})
        assert r.status_code == 200
        assert all(m["kind"] == "chat" for m in r.json()["data"])
        assert (await c.get("/admin/hf/discover", params={"sort": "bogus"})).status_code == 400


async def test_system_info_reports_detected_memory(home):
    from strixrun.memory import MemoryEstimator
    from tests.unit.test_memory import FakeProbe

    class Probe(FakeProbe):
        def gpus(self):
            return [{"name": "NVIDIA GeForce RTX 3090", "total_bytes": 24 * 1024**3, "free_bytes": 23 * 1024**3}]

    a = app()
    async with serve_app(a) as url, httpx.AsyncClient(base_url=url) as c:
        a.state.ctx.estimator = MemoryEstimator(Probe(nvidia_free=23 * 1024**3, available=100 * 1024**3,
                                                      total=128 * 1024**3))
        info = (await c.get("/admin/system")).json()
    assert info["gpus"][0]["name"] == "NVIDIA GeForce RTX 3090"
    assert info["ram_total_bytes"] == 128 * 1024**3
    assert info["default_limit"] == "vram"


async def test_fit_endpoint(home):
    from strixrun.memory import MemoryEstimator
    from tests.unit.test_memory import FakeProbe

    a = app()
    async with serve_app(a) as url, httpx.AsyncClient(base_url=url) as c:
        a.state.ctx.estimator = MemoryEstimator(FakeProbe(nvidia_free=24 * 1024**3, available=100 * 1024**3))
        r = (await c.post(f"/admin/models/{MID}/fit", json={"preset": {"ctx_per_request": 32768}})).json()
    assert r["fits"] and r["estimate"]["placement"] == "gpu"
