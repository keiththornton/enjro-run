import asyncio
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil
import pytest

from strixrun.backend import procutil

TESTS = Path(__file__).resolve().parents[1]
FAKE = str(TESTS / "fake_llama_server.py")
HARNESS = str(TESTS / "helpers" / "spawn_harness.py")


async def wait_healthy(port: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient() as c:
        while time.monotonic() < deadline:
            try:
                if (await c.get(f"http://127.0.0.1:{port}/health")).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            await asyncio.sleep(0.1)
    raise AssertionError(f"port {port} never became healthy")


def wait_gone(pids: list[int], timeout: float = 5.0) -> list[int]:
    deadline = time.monotonic() + timeout
    alive = pids
    while time.monotonic() < deadline:
        alive = [p for p in pids if psutil.pid_exists(p) and psutil.Process(p).status() != psutil.STATUS_ZOMBIE]
        if not alive:
            return []
        time.sleep(0.1)
    return alive


def test_free_port_is_bindable():
    import socket

    port = procutil.free_port()
    with socket.socket() as s:
        s.bind(("127.0.0.1", port))


async def test_spawn_merges_output_and_graceful_stop():
    port = procutil.free_port()
    proc = await procutil.spawn([sys.executable, FAKE, "--port", str(port)])
    await wait_healthy(port)
    line = await asyncio.wait_for(proc.stdout.readline(), 5)
    assert b"loading model" in line
    tree = [proc.pid] + [c.pid for c in psutil.Process(proc.pid).children(recursive=True)]
    assert len(tree) >= 2
    await procutil.stop(proc, grace=10)
    assert wait_gone(tree) == []


async def test_exit_code_propagates_from_command():
    proc = await procutil.spawn([sys.executable, FAKE, "--port", "1"], env={"FAKE_STARTUP_ERROR": "oom"})
    out = await asyncio.wait_for(proc.stdout.read(), 15)
    code = await proc.process.wait()
    assert code == 1
    assert b"out of memory" in out


async def test_stop_kills_tree_when_graceful_stop_hangs():
    # A command that ignores SIGTERM: the stop must escalate to a tree kill.
    script = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('up', flush=True); time.sleep(600)"
    proc = await procutil.spawn([sys.executable, "-c", script])
    await asyncio.wait_for(proc.stdout.readline(), 10)
    tree = [proc.pid] + [c.pid for c in psutil.Process(proc.pid).children(recursive=True)]
    await procutil.stop(proc, grace=1)
    assert wait_gone(tree) == []


def test_killing_parent_kills_children():
    port = procutil.free_port()
    harness = subprocess.Popen([sys.executable, HARNESS, str(port)], stdout=subprocess.PIPE)
    try:
        assert harness.stdout.readline().strip() == b"READY"
        deadline = time.monotonic() + 15
        children: list[int] = []
        while time.monotonic() < deadline:
            children = [c.pid for c in psutil.Process(harness.pid).children(recursive=True)]
            if len(children) >= 2:
                break
            time.sleep(0.1)
        assert len(children) >= 2, "watchdog + fake server should both be running"
        psutil.Process(harness.pid).kill()  # SIGKILL / TerminateProcess: no cleanup code runs
        harness.wait()
        assert wait_gone(children) == []
    finally:
        if harness.poll() is None:
            harness.kill()


@pytest.fixture
def sleeper():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    yield p
    if p.poll() is None:
        p.kill()
        p.wait()


def test_reap_kills_matching_process(tmp_path, sleeper):
    procutil.write_pidfile(tmp_path, "m1", sleeper.pid)
    assert procutil.reap_orphans(tmp_path) == [sleeper.pid]
    assert sleeper.wait(5) is not None
    assert not list(tmp_path.glob("*.pid"))


def test_reap_skips_reused_pid(tmp_path, sleeper):
    import json

    path = procutil.write_pidfile(tmp_path, "m1", sleeper.pid)
    rec = json.loads(path.read_text())
    rec["create_time"] -= 1000  # same pid, different process start: a reused PID
    path.write_text(json.dumps(rec))
    assert procutil.reap_orphans(tmp_path) == []
    assert sleeper.poll() is None
    assert not path.exists()


def test_reap_ignores_dead_and_garbage(tmp_path):
    (tmp_path / "dead.pid").write_text('{"pid": 999999, "create_time": 1, "name": "x"}')
    (tmp_path / "junk.pid").write_text("not json")
    assert procutil.reap_orphans(tmp_path) == []
    assert not list(tmp_path.glob("*.pid"))
