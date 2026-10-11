"""Cross-platform child process plumbing: spawn through the watchdog, stop, PID files, ports.

Layers of orphan protection:
1. The watchdog kills its command when our stdin pipe to it closes (every OS).
2. Windows: the watchdog is assigned to a Job Object with KILL_ON_JOB_CLOSE before
   it starts the command, so the job's handle closing (our death) kills both.
3. Linux: the watchdog gives its command PDEATHSIG=SIGKILL.
4. On startup, PID files left behind are reaped, but only when the pid, create time
   and process name all still match.
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import psutil

from enjrorun.winproc import hidden

STOP_GRACE_S = 10.0


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _WindowsJob:
    """A Job Object that kills every process in it when the last handle closes."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._k32 = k32

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BASIC(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class EXTENDED(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.OpenProcess.restype = wintypes.HANDLE
        self.handle = k32.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = k32.SetInformationJobObject(
            wintypes.HANDLE(self.handle), 9, ctypes.byref(info), ctypes.sizeof(info)
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, pid: int) -> None:
        import ctypes
        from ctypes import wintypes

        PROCESS_SET_QUOTA, PROCESS_TERMINATE = 0x0100, 0x0001
        h = self._k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        if not h:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self._k32.AssignProcessToJobObject(wintypes.HANDLE(self.handle), wintypes.HANDLE(h)):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self._k32.CloseHandle(wintypes.HANDLE(h))


_job: "_WindowsJob | None" = None


def _windows_job() -> "_WindowsJob":
    # One job for the whole Enjro Run process; its handle lives until we exit.
    global _job
    if _job is None:
        _job = _WindowsJob()
    return _job


@dataclass
class Proc:
    process: asyncio.subprocess.Process
    cmd: list[str]

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def returncode(self) -> int | None:
        return self.process.returncode

    @property
    def stdout(self) -> asyncio.StreamReader:
        assert self.process.stdout is not None
        return self.process.stdout


async def spawn(cmd: list[str], env: dict[str, str] | None = None) -> Proc:
    """Start `cmd` under the watchdog. stdout and stderr are merged into Proc.stdout."""
    kwargs: dict = hidden(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
    if os.name != "nt":
        kwargs["start_new_session"] = True
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "enjrorun.backend.watchdog", "--", *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, **(env or {})},
        **kwargs,  # includes hidden(): no console window on Windows
    )
    if os.name == "nt":
        _windows_job().assign(process.pid)
    assert process.stdin is not None
    process.stdin.write(b"go\n")
    await process.stdin.drain()
    return Proc(process, cmd)


def _kill_tree(pid: int) -> None:
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    procs = root.children(recursive=True) + [root]
    for p in procs:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(procs, timeout=5)


async def stop(proc: Proc, grace: float = STOP_GRACE_S) -> int | None:
    """Graceful stop (close the watchdog's stdin), then kill the whole tree after `grace`."""
    if proc.returncode is None:
        stdin = proc.process.stdin
        if stdin is not None and not stdin.is_closing():
            stdin.close()
        try:
            await asyncio.wait_for(proc.process.wait(), grace)
        except TimeoutError:
            _kill_tree(proc.pid)
            await proc.process.wait()
    return proc.returncode


# --- PID files ---------------------------------------------------------------------------


def write_pidfile(directory: Path, name: str, pid: int) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = psutil.Process(pid)
    path = directory / f"{name}.pid"
    path.write_text(json.dumps({"pid": pid, "create_time": p.create_time(), "name": p.name()}))
    return path


def remove_pidfile(directory: Path, name: str) -> None:
    (directory / f"{name}.pid").unlink(missing_ok=True)


def reap_orphans(directory: Path) -> list[int]:
    """Kill processes recorded in PID files if they're provably the same process. Returns killed pids."""
    killed: list[int] = []
    if not directory.is_dir():
        return killed
    for path in directory.glob("*.pid"):
        try:
            rec = json.loads(path.read_text())
            p = psutil.Process(int(rec["pid"]))
            same = abs(p.create_time() - float(rec["create_time"])) < 1.0 and p.name() == rec["name"]
            if same:
                _kill_tree(p.pid)
                killed.append(p.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError, KeyError, OSError):
            pass
        path.unlink(missing_ok=True)
    return killed
