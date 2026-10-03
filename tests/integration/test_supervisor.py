import asyncio
import json
import re
import sys
from pathlib import Path

import httpx
import pytest

from strixrun import errors as E
from strixrun.backend.base import ModelStatus
from strixrun.backend.supervisor import LocalProcessBackend, classify_crash
from strixrun.events import EventBus
from strixrun.presets import Preset
from strixrun.registry import ModelEntry

from .test_procutil import is_alive

FAKE = str(Path(__file__).resolve().parents[1] / "fake_llama_server.py")


def model(mid="test--fake--q4"):
    return ModelEntry(id=mid, aliases=[mid], paths=[f"/models/{mid}.gguf"], size_bytes=1)


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def backend(tmp_path, bus):
    b = LocalProcessBackend(
        bus=bus,
        binary=lambda: [sys.executable, FAKE],
        templates_dir=tmp_path / "templates",
        pid_dir=tmp_path / "pids",
        state_path=tmp_path / "state.json",
        load_timeout=20,
    )
    return b


@pytest.fixture(autouse=True)
async def _cleanup(backend):
    yield
    await backend.shutdown()


async def test_load_ready_target_logs_and_unload(backend, bus, tmp_path):
    sub = bus.subscribe({"model.state"})
    m = model()
    await backend.load(m, Preset())
    state = await backend.wait_settled(m.id)
    assert state.status is ModelStatus.READY
    assert backend.loaded() == [m.id]

    t = backend.target(m.id)
    async with httpx.AsyncClient() as c:
        assert (await c.get(f"{t.base_url}/props", headers=t.headers)).status_code == 200
        assert (await c.get(f"{t.base_url}/props")).status_code == 401  # child requires its own key
    assert t.upstream_model_name == m.id
    assert any("listening" in line for line in backend.logs(m.id))
    assert json.loads((tmp_path / "state.json").read_text())["loaded"] == [m.id]
    assert (tmp_path / "pids" / f"{m.id}.pid").exists()

    pid = state.pid
    await backend.unload(m.id)
    assert backend.state(m.id).status is ModelStatus.STOPPED
    assert not is_alive(pid)
    assert not (tmp_path / "pids" / f"{m.id}.pid").exists()
    assert json.loads((tmp_path / "state.json").read_text())["loaded"] == []

    statuses = []
    while not sub.empty():
        statuses.append((await sub.get()).data["status"])
    assert statuses[:2] == ["loading", "ready"] and statuses[-1] == "stopped"


async def test_command_line_uses_preset(backend):
    m = model()
    await backend.load(m, Preset(ctx_per_request=1024, parallel=3, extra_args=["--threads", "2"]))
    await backend.wait_settled(m.id)
    cmd = backend.command(m.id)
    assert cmd[cmd.index("-c") + 1] == "3072"
    assert cmd[cmd.index("-m") + 1] == m.primary_path
    assert cmd[cmd.index("--host") + 1] == "127.0.0.1"
    assert "--api-key" in cmd and cmd[-2:] == ["--threads", "2"]
    # regression: a urlsafe key could start with "-" and be parsed as a flag (~1.5% of loads)
    assert re.fullmatch(r"[0-9a-f]{48}", cmd[cmd.index("--api-key") + 1])


async def test_load_timeout_marks_crashed_and_kills(backend, monkeypatch):
    monkeypatch.setenv("FAKE_LOAD_DELAY", "30")
    backend.load_timeout = 1.5
    m = model()
    await backend.load(m, Preset())
    state = await backend.wait_settled(m.id)
    assert state.status is ModelStatus.CRASHED
    assert "timed out" in state.reason
    assert m.id not in backend.loaded()


@pytest.mark.parametrize(
    "signature,crash_class",
    [("oom", "out_of_memory"), ("unknown_arch", "unknown_architecture"), ("cudart", "missing_cuda_runtime")],
)
async def test_early_exit_is_classified(backend, monkeypatch, signature, crash_class):
    monkeypatch.setenv("FAKE_STARTUP_ERROR", signature)
    m = model()
    await backend.load(m, Preset())
    state = await backend.wait_settled(m.id)
    assert state.status is ModelStatus.CRASHED
    assert state.crash_class == crash_class, (state.reason, backend.logs(m.id))
    assert any(line for line in backend.logs(m.id))


async def test_port_conflict_retries_then_fails(backend, monkeypatch):
    monkeypatch.setenv("FAKE_STARTUP_ERROR", "port")
    m = model()
    await backend.load(m, Preset())
    state = await backend.wait_settled(m.id)
    assert state.crash_class == "port_in_use"
    assert backend.attempts(m.id) == 3


async def test_crash_after_ready(backend, monkeypatch):
    monkeypatch.setenv("FAKE_CRASH_AFTER", "1")
    m = model()
    await backend.load(m, Preset())
    await backend.wait_settled(m.id)
    t = backend.target(m.id)
    async with httpx.AsyncClient() as c:
        with pytest.raises(httpx.HTTPError):
            async with c.stream(
                "POST", f"{t.base_url}/v1/chat/completions", headers=t.headers,
                json={"model": "x", "stream": True, "messages": []},
            ) as r:
                async for _ in r.aiter_bytes():
                    pass
    for _ in range(50):
        if backend.state(m.id).status is ModelStatus.CRASHED:
            break
        await asyncio.sleep(0.1)
    assert backend.state(m.id).status is ModelStatus.CRASHED
    assert "simulated crash" in "\n".join(backend.logs(m.id))


async def test_shutdown_keeps_state_for_restore(backend, tmp_path):
    m = model()
    await backend.load(m, Preset())
    await backend.wait_settled(m.id)
    await backend.shutdown()
    assert LocalProcessBackend.read_state(tmp_path / "state.json") == [m.id]


async def test_binary_missing(tmp_path, bus):
    b = LocalProcessBackend(
        bus=bus, binary=lambda: None, templates_dir=tmp_path, pid_dir=tmp_path / "p",
        state_path=tmp_path / "s.json",
    )
    with pytest.raises(E.BinaryMissing):
        await b.load(model(), Preset())


async def test_double_load_rejected(backend):
    m = model()
    await backend.load(m, Preset())
    with pytest.raises(E.AlreadyLoaded):
        await backend.load(m, Preset())


async def test_unload_during_loading(backend, monkeypatch):
    monkeypatch.setenv("FAKE_LOAD_DELAY", "30")
    m = model()
    await backend.load(m, Preset())
    await asyncio.sleep(0.5)
    await backend.unload(m.id)
    assert backend.state(m.id).status is ModelStatus.STOPPED


def test_classify_crash_windows_dll_exit_code():
    assert classify_crash([], 3221225781)[0] == "missing_cuda_runtime"
    assert classify_crash(["something odd"], 1)[0] == "generic"
