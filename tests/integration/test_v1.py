import json
import subprocess
import sys
from pathlib import Path

import httpx
import openai
import pytest

from strixrun import paths
from strixrun.app import create_app
from strixrun.backend import procutil
from strixrun.backend.base import ModelStatus
from strixrun.presets import Preset, PresetStore
from strixrun.registry import ModelEntry, Registry
from strixrun.settings import SettingsError, SettingsStore

from .conftest import serve_app

FAKE = str(Path(__file__).resolve().parents[1] / "fake_llama_server.py")
CHAT_ID, CHAT_ALIAS = "test--chat--q4", "chat:q4"
EMB_ID, EMB_ALIAS = "test--emb--q4", "emb:q4"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STRIX_HOME", str(tmp_path / "strix"))
    reg = Registry(paths.registry_path())
    reg.add(ModelEntry(id=CHAT_ID, aliases=[CHAT_ALIAS, "gpt-4o-mini"], paths=["/m/chat.gguf"], size_bytes=1))
    reg.add(ModelEntry(id=EMB_ID, aliases=[EMB_ALIAS], paths=["/m/emb.gguf"], size_bytes=1))
    PresetStore(paths.presets_dir()).save(EMB_ID, Preset(mode="embedding"))
    return tmp_path


def app():
    return create_app(binary=lambda: [sys.executable, FAKE], load_timeout=20)


async def load(application, model_id):
    ctx = application.state.ctx
    entry = ctx.registry.get(model_id)
    await ctx.backend.load(entry, ctx.presets.load(model_id, {}))
    assert (await ctx.backend.wait_settled(model_id)).status is ModelStatus.READY


async def test_models_empty_shape_and_security_headers(home):
    a = app()
    async with serve_app(a) as url, httpx.AsyncClient() as c:
        r = await c.get(f"{url}/v1/models")
        assert r.json() == {"object": "list", "data": []}
        assert "default-src 'self'" in r.headers["content-security-policy"]
        assert r.headers["x-content-type-options"] == "nosniff"
        root = await c.get(f"{url}/")
        assert root.status_code == 200  # no web_dist built: placeholder, not a crash


async def test_openai_sdk_chat_stream_embeddings(home):
    a = app()
    async with serve_app(a) as url:
        await load(a, CHAT_ID)
        await load(a, EMB_ID)
        client = openai.AsyncOpenAI(base_url=f"{url}/v1", api_key="unused-on-loopback")

        models = await client.models.list()
        ids = {m.id for m in models.data}
        assert {CHAT_ALIAS, "gpt-4o-mini", EMB_ALIAS} <= ids

        r = await client.chat.completions.create(
            model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}]
        )
        assert r.choices[0].message.content.startswith("tok0")
        assert r.usage.completion_tokens == 5

        text = ""
        stream = await client.chat.completions.create(
            model=CHAT_ALIAS, messages=[{"role": "user", "content": "hi"}], stream=True
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                text += chunk.choices[0].delta.content
        assert text.startswith("tok0 tok1")

        tools = [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}]
        r = await client.chat.completions.create(
            model=CHAT_ALIAS, messages=[{"role": "user", "content": "weather?"}], tools=tools
        )
        assert r.choices[0].finish_reason == "tool_calls"
        assert r.choices[0].message.tool_calls[0].function.name == "get_weather"

        e = await client.embeddings.create(model=EMB_ALIAS, input=["a", "b"])
        assert len(e.data) == 2

        with pytest.raises(openai.BadRequestError):
            await client.embeddings.create(model=CHAT_ALIAS, input="a")
        with pytest.raises(openai.NotFoundError):
            await client.chat.completions.create(model="nope", messages=[])


async def test_models_all_lists_unloaded_with_status(home):
    async with serve_app(app()) as url, httpx.AsyncClient() as c:
        data = (await c.get(f"{url}/v1/models?all=1")).json()["data"]
    by_id = {d["id"]: d for d in data}
    assert by_id[CHAT_ALIAS]["status"] == "stopped"
    assert by_id[CHAT_ALIAS]["strixrun_id"] == CHAT_ID


async def test_not_loaded_error_through_sdk(home):
    async with serve_app(app()) as url:
        client = openai.AsyncOpenAI(base_url=f"{url}/v1", api_key="x")
        with pytest.raises(openai.ConflictError) as exc:
            await client.chat.completions.create(model=CHAT_ALIAS, messages=[])
    assert "strixrun load chat:q4" in str(exc.value)


async def test_unknown_api_path_is_json_404_not_spa(home):
    async with serve_app(app()) as url, httpx.AsyncClient() as c:
        r = await c.get(f"{url}/v1/nothing")
    assert r.status_code == 404


async def test_restore_loaded_on_startup(home):
    paths.state_path().parent.mkdir(parents=True, exist_ok=True)
    paths.state_path().write_text(json.dumps({"loaded": [CHAT_ID, "gone--model--q4"]}))
    a = app()
    async with serve_app(a):
        ctx = a.state.ctx
        state = await ctx.backend.wait_settled(CHAT_ID)
        assert state.status is ModelStatus.READY
    # shutdown keeps the set for the next start
    assert json.loads(paths.state_path().read_text())["loaded"] == [CHAT_ID]


async def test_restore_disabled(home):
    store = SettingsStore()
    s = store.load()
    s.restore_loaded = False
    store.save(s)
    paths.state_path().parent.mkdir(parents=True, exist_ok=True)
    paths.state_path().write_text(json.dumps({"loaded": [CHAT_ID]}))
    a = create_app(binary=lambda: [sys.executable, FAKE])
    async with serve_app(a):
        assert a.state.ctx.backend.loaded() == []


async def test_startup_reaps_orphans(home):
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    try:
        procutil.write_pidfile(paths.pid_dir(), "old--model--q4", sleeper.pid)
        async with serve_app(app()):
            pass
        assert sleeper.wait(5) is not None
    finally:
        if sleeper.poll() is None:
            sleeper.kill()


def test_lan_host_without_key_refuses_to_start(home):
    store = SettingsStore()
    s = store.load()
    s.host = "0.0.0.0"
    store.save(s)
    with pytest.raises(SettingsError, match="strixrun key set"):
        create_app()


async def test_scan_dirs_are_registered_on_startup(home):
    d = home / "lmstudio"
    d.mkdir()
    (d / "Other-Model.Q4_K_M.gguf").write_bytes(b"x")
    store = SettingsStore()
    s = store.load()
    s.extra_scan_dirs = [str(d)]
    store.save(s)
    a = create_app(binary=lambda: [sys.executable, FAKE])
    async with serve_app(a):
        scanned = [m for m in a.state.ctx.registry.all() if m.location == "scan"]
    assert len(scanned) == 1
