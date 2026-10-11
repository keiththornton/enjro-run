"""End-to-end: real llama-server, real models, official OpenAI SDK.

Opt-in: uv run pytest -m e2e
ENJRORUN_E2E_HOME selects the Enjro Run home (default ~/.cache/enjrorun-e2e), so the
binary and ~1.2 GB of models are downloaded once and reused (CI caches this folder).
"""

import asyncio
import json
import os
import time
from pathlib import Path

import httpx
import openai
import pytest
import pytest_asyncio

from enjrorun.app import create_app
from tests.integration.conftest import serve_app

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="module")]

CHAT_REPO, CHAT_QUANT, CHAT_ALIAS = "Qwen/Qwen2.5-1.5B-Instruct-GGUF", "Q4_K_M", "qwen2.5-1.5b-instruct:q4_k_m"
EMB_REPO, EMB_QUANT, EMB_ALIAS = "nomic-ai/nomic-embed-text-v1.5-GGUF", "Q4_K_M", "nomic-embed-text-v1.5:q4_k_m"
SETUP_TIMEOUT_S = 1800


async def wait_for(predicate, timeout: float, what: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(1)
    raise TimeoutError(what)


@pytest.fixture(scope="module")
def e2e_home():
    home = Path(os.environ.get("ENJRORUN_E2E_HOME", Path.home() / ".cache" / "enjrorun-e2e"))
    old = os.environ.get("ENJRO_HOME")
    os.environ["ENJRO_HOME"] = str(home)
    yield home
    if old is None:
        os.environ.pop("ENJRO_HOME", None)
    else:
        os.environ["ENJRO_HOME"] = old


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def server(e2e_home):
    app = create_app(load_timeout=600)
    async with serve_app(app) as url:
        ctx = app.state.ctx
        await wait_for(ctx.binaries.command, SETUP_TIMEOUT_S, "llama-server install")
        for repo, quant in ((CHAT_REPO, CHAT_QUANT), (EMB_REPO, EMB_QUANT)):
            owner, name = repo.split("/")
            from enjrorun.registry import make_id

            model_id = make_id(owner, name, quant)
            if ctx.registry.get(model_id) is None:
                job = await ctx.downloads.start(repo, quant)
                await wait_for(lambda j=job: j.status in ("done", "error", "cancelled"), SETUP_TIMEOUT_S,
                               f"download {repo}")
                assert job.status == "done", job.error
        async with httpx.AsyncClient(base_url=url, timeout=600) as c:
            for alias in (CHAT_ALIAS, EMB_ALIAS):
                entry = ctx.registry.resolve(alias)
                r = await c.post(f"/admin/models/{entry.id}/load", json={"confirm": True})
                assert r.status_code in (202, 409), r.text
                state = await ctx.backend.wait_settled(entry.id)
                assert state.status.value == "ready", (state.reason, ctx.backend.logs(entry.id)[-20:])
        yield url, ctx


@pytest.fixture
def client(server):
    url, _ = server
    return openai.AsyncOpenAI(base_url=f"{url}/v1", api_key="loopback-needs-no-key", timeout=600)


async def test_models_listed(client):
    ids = {m.id for m in (await client.models.list()).data}
    assert {CHAT_ALIAS, EMB_ALIAS} <= ids


async def test_chat(client):
    # A factual prompt: this checks the server path, not the model's instruction-following.
    r = await client.chat.completions.create(
        model=CHAT_ALIAS, temperature=0, seed=7, max_tokens=16,
        messages=[{"role": "user", "content": "What is 2+2? Answer with just the number."}],
    )
    assert "4" in r.choices[0].message.content
    assert r.usage.completion_tokens > 0


async def test_stream_and_request_log(client, server):
    _, ctx = server
    text = ""
    stream = await client.chat.completions.create(
        model=CHAT_ALIAS, temperature=0, seed=7, max_tokens=32, stream=True,
        messages=[{"role": "user", "content": "Count from 1 to 5, separated by spaces."}],
    )
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            text += chunk.choices[0].delta.content
    assert "3" in text
    # The record is published just after the last byte reaches the client: wait for it.
    rows = await wait_for(lambda: [r for r in ctx.requestlog.list(limit=5) if r["stream"]], 5, "stream record")
    row = rows[0]
    assert row["ttft_ms"] and row["completion_tokens"] and row["tok_per_s"], row


async def test_tool_call(client):
    tools = [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
        },
    }]
    r = await client.chat.completions.create(
        model=CHAT_ALIAS, temperature=0, seed=7, tools=tools, tool_choice="required",
        messages=[{"role": "user", "content": "What's the weather in Paris right now?"}],
    )
    choice = r.choices[0]
    assert choice.message.tool_calls, choice
    call = choice.message.tool_calls[0]
    assert call.function.name == "get_weather"
    assert "paris" in json.loads(call.function.arguments)["city"].lower()


async def test_json_schema(client):
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        "required": ["name", "age"],
        "additionalProperties": False,
    }
    r = await client.chat.completions.create(
        model=CHAT_ALIAS, temperature=0, seed=7, max_tokens=64,
        response_format={"type": "json_schema", "json_schema": {"name": "person", "schema": schema, "strict": True}},
        messages=[{"role": "user", "content": "Invent a person. Return their name and age."}],
    )
    obj = json.loads(r.choices[0].message.content)
    assert set(obj) == {"name", "age"}
    assert isinstance(obj["name"], str) and isinstance(obj["age"], int)


async def test_embeddings(client):
    r = await client.embeddings.create(model=EMB_ALIAS, input=["search_query: hello", "search_query: world"])
    assert len(r.data) == 2
    assert len(r.data[0].embedding) == 768
