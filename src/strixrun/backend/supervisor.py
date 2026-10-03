"""LocalProcessBackend: one watchdog-wrapped llama-server child per loaded model."""

import asyncio
import json
import logging
import os
import re
import secrets
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from strixrun import errors
from strixrun.backend import procutil
from strixrun.backend.base import ModelState, ModelStatus, Target
from strixrun.events import EventBus
from strixrun.presets import Preset, llama_args
from strixrun.registry import ModelEntry

log = logging.getLogger(__name__)

LOG_LINES = 500
HEALTH_INTERVAL_S = 0.25
PORT_ATTEMPTS = 3
WINDOWS_DLL_NOT_FOUND = (0xC0000135, -1073741515)

CRASH_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("port_in_use", re.compile(r"couldn't bind|address already in use", re.IGNORECASE),
     "the port was already in use"),
    ("missing_cuda_runtime", re.compile(r"cudart|cublas\w*\.(?:dll|so)|STATUS_DLL_NOT_FOUND", re.IGNORECASE),
     "the CUDA runtime libraries are missing; reinstall the binary from Settings"),
    ("out_of_memory", re.compile(
        r"out of memory|failed to allocate|unable to allocate|cudaMalloc failed|"
        r"insufficient memory|kIOGPUCommandBufferCallbackErrorOutOfMemory", re.IGNORECASE),
     "not enough memory; try a smaller context, fewer GPU layers or a smaller quant"),
    ("unknown_architecture", re.compile(r"unknown model architecture", re.IGNORECASE),
     "this llama.cpp version doesn't support the model's architecture; update the binary"),
]


def classify_crash(lines: list[str], exit_code: int | None) -> tuple[str, str]:
    """Return (crash_class, human reason) from the stderr tail and exit code."""
    text = "\n".join(lines[-80:])
    if exit_code is not None and (exit_code & 0xFFFFFFFF) == WINDOWS_DLL_NOT_FOUND[0]:
        return "missing_cuda_runtime", CRASH_PATTERNS[1][2]
    for cls, pattern, reason in CRASH_PATTERNS:
        if pattern.search(text):
            return cls, reason
    return "generic", f"llama-server exited (code {exit_code}); see the model's logs"


@dataclass
class _Child:
    model: ModelEntry
    preset: Preset
    state: ModelState = field(default_factory=ModelState)
    logs: deque = field(default_factory=lambda: deque(maxlen=LOG_LINES))
    proc: procutil.Proc | None = None
    api_key: str = ""
    cmd: list[str] = field(default_factory=list)
    attempts: int = 0
    settled: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None


class LocalProcessBackend:
    def __init__(
        self,
        bus: EventBus,
        binary: Callable[[], list[str] | None],
        templates_dir: Path,
        pid_dir: Path,
        state_path: Path,
        load_timeout: float = 300.0,
    ) -> None:
        self.bus = bus
        self._binary = binary
        self.templates_dir = Path(templates_dir)
        self.pid_dir = Path(pid_dir)
        self.state_path = Path(state_path)
        self.load_timeout = load_timeout
        self._children: dict[str, _Child] = {}

    # ---- public API -------------------------------------------------------------------

    async def load(self, model: ModelEntry, preset: Preset) -> None:
        current = self._children.get(model.id)
        if current and current.state.status in (ModelStatus.LOADING, ModelStatus.READY):
            raise errors.AlreadyLoaded(model.aliases[0] if model.aliases else model.id)
        binary = self._binary()
        if not binary:
            raise errors.BinaryMissing()
        llama_args(preset, self.templates_dir)  # validate before touching state
        child = _Child(model=model, preset=preset)
        self._children[model.id] = child
        self._set(child, ModelStatus.LOADING)
        child.task = asyncio.create_task(self._run(child, binary))

    async def wait_settled(self, model_id: str) -> ModelState:
        child = self._children[model_id]
        await child.settled.wait()
        return child.state

    async def unload(self, model_id: str) -> None:
        child = self._children.get(model_id)
        if child is None or child.state.status in (ModelStatus.STOPPED, ModelStatus.CRASHED):
            if child:
                self._set(child, ModelStatus.STOPPED)
            return
        await self._stop(child)
        self._save_state()

    def state(self, model_id: str) -> ModelState:
        child = self._children.get(model_id)
        return child.state if child else ModelState()

    def target(self, model_id: str) -> Target:
        child = self._children[model_id]
        return Target(
            base_url=f"http://127.0.0.1:{child.state.port}",
            upstream_model_name=model_id,
            headers={"Authorization": f"Bearer {child.api_key}"},
        )

    def preset(self, model_id: str) -> Preset | None:
        child = self._children.get(model_id)
        return child.preset if child else None

    def logs(self, model_id: str) -> list[str]:
        child = self._children.get(model_id)
        return list(child.logs) if child else []

    def command(self, model_id: str) -> list[str]:
        return list(self._children[model_id].cmd)

    def attempts(self, model_id: str) -> int:
        return self._children[model_id].attempts

    def loaded(self) -> list[str]:
        return [
            mid for mid, c in self._children.items()
            if c.state.status in (ModelStatus.LOADING, ModelStatus.READY)
        ]

    async def shutdown(self) -> None:
        """Stop every child without changing state.json, so restore-on-restart works."""
        self._save_state()
        await asyncio.gather(
            *(self._stop(c, persist=False) for c in self._children.values()
              if c.state.status in (ModelStatus.LOADING, ModelStatus.READY)),
            return_exceptions=True,
        )

    @staticmethod
    def read_state(path: Path) -> list[str]:
        try:
            return list(json.loads(Path(path).read_text()).get("loaded", []))
        except (OSError, ValueError):
            return []

    # ---- internals --------------------------------------------------------------------

    def _set(self, child: _Child, status: ModelStatus, reason: str | None = None,
             crash_class: str | None = None) -> None:
        s = child.state
        s.status, s.reason, s.crash_class = status, reason, crash_class
        if status is ModelStatus.READY:
            s.ready_at = time.time()
        if status in (ModelStatus.READY, ModelStatus.STOPPED, ModelStatus.CRASHED):
            child.settled.set()
        self.bus.publish("model.state", {"id": child.model.id, **s.to_dict()})

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"loaded": self.loaded()}))
        os.replace(tmp, self.state_path)

    async def _run(self, child: _Child, binary: list[str]) -> None:
        try:
            for attempt in range(1, PORT_ATTEMPTS + 1):
                child.attempts = attempt
                exit_code = await self._attempt(child, binary)
                if child.state.status is not ModelStatus.LOADING:
                    break  # ready/stopping/stopped: _attempt handled it
                cls, reason = classify_crash(list(child.logs), exit_code)
                if cls == "port_in_use" and attempt < PORT_ATTEMPTS:
                    child.logs.append(f"strixrun: port in use, retrying ({attempt}/{PORT_ATTEMPTS})")
                    continue
                self._crashed(child, cls, reason)
                break
        except asyncio.CancelledError:
            raise
        except Exception as e:  # never leave a child stuck in LOADING
            log.exception("load of %s failed", child.model.id)
            child.logs.append(f"strixrun: internal error: {e!r}")
            self._crashed(child, "generic", f"internal error: {e}")

    def _crashed(self, child: _Child, cls: str, reason: str) -> None:
        self._set(child, ModelStatus.CRASHED, reason, cls)
        procutil.remove_pidfile(self.pid_dir, child.model.id)
        self._save_state()

    async def _attempt(self, child: _Child, binary: list[str]) -> int | None:
        """One spawn. Returns the exit code if the process died while loading."""
        port = procutil.free_port()
        child.api_key = secrets.token_urlsafe(24)
        child.cmd = [
            *binary, "-m", child.model.primary_path,
            "--host", "127.0.0.1", "--port", str(port),
            "--api-key", child.api_key,
            *llama_args(child.preset, self.templates_dir),
        ]
        child.proc = await procutil.spawn(child.cmd)
        child.state.pid, child.state.port, child.state.started_at = child.proc.pid, port, time.time()
        procutil.write_pidfile(self.pid_dir, child.model.id, child.proc.pid)
        reader = asyncio.create_task(self._read_logs(child, child.proc))

        exited = asyncio.create_task(child.proc.process.wait())
        healthy = asyncio.create_task(self._poll_health(port))
        done, _ = await asyncio.wait({exited, healthy}, timeout=self.load_timeout,
                                     return_when=asyncio.FIRST_COMPLETED)
        if healthy in done and healthy.result():
            exited.cancel()
            self._set(child, ModelStatus.READY)
            self._save_state()
            await self._watch_exit(child, reader)
            return None
        healthy.cancel()
        if exited in done:
            await reader
            return exited.result()
        # timeout
        exited.cancel()
        await procutil.stop(child.proc, grace=2)
        await reader
        self._crashed(child, "load_timeout", f"load timed out after {self.load_timeout:g}s")
        return None

    async def _watch_exit(self, child: _Child, reader: asyncio.Task) -> None:
        proc = child.proc
        code = await proc.process.wait()
        await reader
        if child.state.status is ModelStatus.READY:
            cls, reason = classify_crash(list(child.logs), code)
            self._crashed(child, cls, reason)

    async def _poll_health(self, port: int) -> bool:
        async with httpx.AsyncClient(timeout=2.0) as client:
            while True:
                try:
                    r = await client.get(f"http://127.0.0.1:{port}/health")
                    if r.status_code == 200:
                        return True
                except httpx.TransportError:
                    pass
                await asyncio.sleep(HEALTH_INTERVAL_S)

    async def _read_logs(self, child: _Child, proc: procutil.Proc) -> None:
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                return
            line = raw.decode("utf-8", "replace").rstrip()
            child.logs.append(line)
            self.bus.publish("model.log", {"id": child.model.id, "line": line})

    async def _stop(self, child: _Child, persist: bool = True) -> None:
        self._set(child, ModelStatus.STOPPING)
        if child.task and not child.task.done() and child.state.ready_at is None:
            child.task.cancel()
        if child.proc is not None:
            await procutil.stop(child.proc)
        if child.task:
            try:
                await child.task
            except asyncio.CancelledError:
                pass
            except Exception:  # the task's failure is already recorded in state
                log.debug("load task for %s ended with an error", child.model.id, exc_info=True)
        procutil.remove_pidfile(self.pid_dir, child.model.id)
        self._set(child, ModelStatus.STOPPED)
        if persist:
            self._save_state()
