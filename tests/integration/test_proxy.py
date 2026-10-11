import asyncio
import json
from dataclasses import dataclass, field

import httpx
import pytest
from fastapi import FastAPI, Request

from enjrorun import errors as E
from enjrorun.backend.base import ModelState, ModelStatus, Target
from enjrorun.events import EventBus
from enjrorun.presets import Preset
from enjrorun.proxy import Proxy, RequestMeta
from enjrorun.registry import ModelEntry, Registry

from .conftest import fake_server, serve_app

MID = "test--fake--q4"
ALIAS = "fake:q4"


@dataclass
class StubBackend:
    states: dict = field(default_factory=dict)
    targets: dict = field(default_factory=dict)
    presets: dict = field(default_factory=dict)

    def state(self, mid):
        return self.states.get(mid, ModelState())

    def target(self, mid):
        return self.targets[mid]

    def preset(self, mid):
        return self.presets.get(mid)


@pytest.fixture
def registry(tmp_path):
    reg = Registry(tmp_path / "models.json")
    reg.add(ModelEntry(id=MID, aliases=[ALIAS], paths=["/m.gguf"], size_bytes=1))
    return reg


@pytest.fixture
def bus():
    return EventBus()


def make_app(proxy: Proxy) -> FastAPI:
    app = FastAPI()
    E.install_handlers(app)

    async def handle(request: Request, endpoint: str):
        meta = RequestMeta(client_ip=request.client.host, key_id=None)
        return await proxy.handle(endpoint, await request.json(), meta)

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        return await handle(request, "chat")

    @app.post("/v1/embeddings")
    async def emb(request: Request):
        return await handle(request, "embeddings")

    return app


def ready_backend(url: str, key: str, preset: Preset | None = None) -> StubBackend:
    return StubBackend(
        states={MID: ModelState(status=ModelStatus.READY, ready_at=1.0)},
        targets={MID: Target(base_url=url, upstream_model_name=MID, headers={"Authorization": f"Bearer {key}"})},
        presets={MID: preset or Preset()},
    )


def records(bus: EventBus):
    seen = []
    bus.on("request.completed", lambda ev: seen.append(ev.data))
    return seen


CHAT = {"model": ALIAS, "messages": [{"role": "user", "content": "hi"}]}


async def post(url, body, **kw):
    async with httpx.AsyncClient(timeout=30) as c:
        return await c.post(f"{url}/v1/chat/completions", json=body, **kw)


# ---- resolution -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state,status,code",
    [
        (None, 409, "model_not_loaded"),
        (ModelState(status=ModelStatus.LOADING), 503, "model_loading"),
        (ModelState(status=ModelStatus.CRASHED, reason="out of memory"), 409, "model_crashed"),
        (ModelState(status=ModelStatus.STOPPED), 409, "model_not_loaded"),
    ],
)
async def test_resolution_errors(registry, bus, state, status, code):
    backend = StubBackend(states={MID: state} if state else {})
    async with serve_app(make_app(Proxy(registry, backend, bus))) as url:
        r = await post(url, CHAT)
    assert r.status_code == status
    assert r.json()["error"]["code"] == code


async def test_unknown_model_404(registry, bus):
    async with serve_app(make_app(Proxy(registry, StubBackend(), bus))) as url:
        r = await post(url, {**CHAT, "model": "nope"})
    assert r.status_code == 404


async def test_missing_model_field_400(registry, bus):
    async with serve_app(make_app(Proxy(registry, StubBackend(), bus))) as url:
        r = await post(url, {"messages": []})
    assert r.status_code == 400


async def test_mode_mismatch_400(registry, bus):
    async with fake_server() as (furl, key, _):
        backend = ready_backend(furl, key, Preset(mode="embedding"))
        async with serve_app(make_app(Proxy(registry, backend, bus))) as url:
            r = await post(url, CHAT)
    assert r.status_code == 400
    assert "embedding" in r.json()["error"]["message"]


# ---- non-streaming ----------------------------------------------------------------------


async def test_non_stream_passthrough_and_record(registry, bus):
    seen = records(bus)
    async with fake_server() as (furl, key, _):
        async with serve_app(make_app(Proxy(registry, ready_backend(furl, key), bus))) as url:
            r = await post(url, CHAT)
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"].startswith("tok0")
    rec = seen[-1]
    assert rec.model_id == MID and rec.alias_used == ALIAS
    assert rec.status == "ok" and rec.http_status == 200
    assert (rec.prompt_tokens, rec.completion_tokens) == (7, 5)
    assert rec.stream is False and rec.endpoint == "chat"
    assert rec.request_body["model"] == ALIAS  # the client's view, not the rewritten one
    assert r.json()["model"] == ALIAS  # never the child's internal name (or a file path)


async def test_upstream_error_status_passed_through(registry, bus):
    seen = records(bus)
    async with fake_server(api_key="right") as (furl, _, _):
        backend = ready_backend(furl, "wrong")  # child rejects with 401
        async with serve_app(make_app(Proxy(registry, backend, bus))) as url:
            r = await post(url, CHAT)
    assert r.status_code == 401
    assert seen[-1].status == "error"


async def test_502_when_child_unreachable(registry, bus):
    from enjrorun.backend import procutil

    backend = ready_backend(f"http://127.0.0.1:{procutil.free_port()}", "k")
    async with serve_app(make_app(Proxy(registry, backend, bus))) as url:
        r = await post(url, CHAT)
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "upstream_error"


async def test_embeddings(registry, bus):
    async with fake_server() as (furl, key, _):
        backend = ready_backend(furl, key, Preset(mode="embedding"))
        async with serve_app(make_app(Proxy(registry, backend, bus))) as url, httpx.AsyncClient() as c:
            r = await c.post(f"{url}/v1/embeddings", json={"model": ALIAS, "input": ["a", "b"]})
    assert r.status_code == 200 and len(r.json()["data"]) == 2


# ---- streaming --------------------------------------------------------------------------


async def read_stream(url, body, **kw):
    events = []
    async with httpx.AsyncClient(timeout=30) as c:
        async with c.stream("POST", f"{url}/v1/chat/completions", json=body, **kw) as r:
            headers = r.headers
            async for line in r.aiter_lines():
                if line.startswith("data: "):
                    events.append(line[6:])
    return headers, events


async def test_stream_headers_usage_stripped_when_not_requested(registry, bus):
    seen = records(bus)
    async with fake_server() as (furl, key, _):
        async with serve_app(make_app(Proxy(registry, ready_backend(furl, key), bus))) as url:
            headers, events = await read_stream(url, {**CHAT, "stream": True})
    assert headers["content-type"].startswith("text/event-stream")
    assert headers["cache-control"] == "no-cache"
    assert headers["x-accel-buffering"] == "no"
    assert events[-1] == "[DONE]"
    parsed = [json.loads(e) for e in events[:-1]]
    assert all(p["choices"] for p in parsed), "usage-only chunk must be hidden from this client"
    assert {p["model"] for p in parsed} == {ALIAS}
    rec = seen[-1]
    assert rec.completion_tokens == 5 and rec.ttft_ms is not None and rec.stream is True


async def test_stream_usage_kept_when_requested(registry, bus):
    async with fake_server() as (furl, key, _):
        async with serve_app(make_app(Proxy(registry, ready_backend(furl, key), bus))) as url:
            _, events = await read_stream(
                url, {**CHAT, "stream": True, "stream_options": {"include_usage": True}}
            )
    usage = [json.loads(e) for e in events[:-1] if not json.loads(e)["choices"]]
    assert usage and usage[0]["usage"]["completion_tokens"] == 5


async def test_stream_is_incremental(registry, bus):
    async with fake_server(FAKE_TOKEN_DELAY="0.3", FAKE_TOKENS="5") as (furl, key, _):
        async with serve_app(make_app(Proxy(registry, ready_backend(furl, key), bus))) as url:
            loop = asyncio.get_running_loop()
            t0 = loop.time()
            first = None
            async with httpx.AsyncClient(timeout=30) as c:
                async with c.stream("POST", f"{url}/v1/chat/completions", json={**CHAT, "stream": True}) as r:
                    async for line in r.aiter_lines():
                        if '"content": "tok0' in line:
                            first = loop.time() - t0
                    total = loop.time() - t0
    assert first is not None and first < total - 0.8  # first token arrived well before the end


async def test_client_disconnect_cancels_upstream(registry, bus, tmp_path):
    seen = records(bus)
    events_file = tmp_path / "fake_events"
    async with fake_server(FAKE_TOKEN_DELAY="0.2", FAKE_TOKENS="50", FAKE_EVENTS_FILE=str(events_file)) as (furl, key, _):
        async with serve_app(make_app(Proxy(registry, ready_backend(furl, key), bus))) as url:
            async with httpx.AsyncClient(timeout=30) as c:
                async with c.stream("POST", f"{url}/v1/chat/completions", json={**CHAT, "stream": True}) as r:
                    async for line in r.aiter_lines():
                        if "tok1" in line:
                            break  # leaving the block closes the connection mid-stream
            for _ in range(50):
                if events_file.exists() and "cancelled" in events_file.read_text() and seen:
                    break
                await asyncio.sleep(0.1)
    assert "cancelled" in events_file.read_text()
    assert seen[-1].status == "cancelled"


async def test_idle_timeout_sends_error_chunk_and_frees_slot(registry, bus):
    seen = records(bus)
    async with fake_server(FAKE_HANG_AFTER="1") as (furl, key, _):
        backend = ready_backend(furl, key, Preset(parallel=1))
        proxy = Proxy(registry, backend, bus, idle_timeout=lambda: 1.0)
        async with serve_app(make_app(proxy)) as url:
            _, events = await read_stream(url, {**CHAT, "stream": True})
            assert "error" in json.loads(events[-1])
            assert seen[-1].status == "error" and "idle" in seen[-1].error
            # the slot was released: a non-streaming call gets through
            r = await post(url, CHAT)
            assert r.status_code == 200


async def test_crash_mid_stream_sends_error_chunk(registry, bus):
    async with fake_server(FAKE_CRASH_AFTER="2") as (furl, key, _):
        async with serve_app(make_app(Proxy(registry, ready_backend(furl, key), bus))) as url:
            _, events = await read_stream(url, {**CHAT, "stream": True})
    assert any('"tok0' in e for e in events)
    last = json.loads(events[-1])
    assert last["error"]["code"] == "upstream_error"


# ---- concurrency ------------------------------------------------------------------------


async def test_queue_full_429_and_queue_ms(registry, bus):
    seen = records(bus)
    async with fake_server(FAKE_TOKEN_DELAY="0.3", FAKE_TOKENS="4") as (furl, key, _):
        backend = ready_backend(furl, key, Preset(parallel=1, queue_max=1))
        async with serve_app(make_app(Proxy(registry, backend, bus))) as url:
            a = asyncio.create_task(post(url, CHAT))
            await asyncio.sleep(0.3)
            b = asyncio.create_task(post(url, CHAT))
            await asyncio.sleep(0.3)
            c = await post(url, CHAT)
            assert c.status_code == 429
            assert c.json()["error"]["code"] == "queue_full"
            ra, rb = await a, await b
    assert ra.status_code == rb.status_code == 200
    assert max(r.queue_ms for r in seen) > 500
