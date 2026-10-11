"""Child-process wrapper that guarantees llama-server dies with Enjro Run.

Usage: python -m enjrorun.backend.watchdog -- <command...>

Protocol on stdin:
- The watchdog waits for a line "go" before starting the command. This gives the
  parent time to put the watchdog in a Windows Job Object first, so the command is
  created inside the job.
- When stdin reaches EOF, the command is terminated and the watchdog exits. The
  parent closes the pipe to stop gracefully. If the parent dies for any reason, the
  OS closes the pipe, so the child is cleaned up on every platform.

The watchdog exits with the command's exit code once the command finishes.
"""

import os
import signal
import subprocess
import sys
import threading

STOP_TIMEOUT_S = 10.0


def _linux_pdeathsig() -> None:
    # If the watchdog itself is SIGKILLed, take the command down too.
    import ctypes

    PR_SET_PDEATHSIG = 1
    ctypes.CDLL("libc.so.6", use_errno=True).prctl(PR_SET_PDEATHSIG, signal.SIGKILL)


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(STOP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def main(argv: list[str]) -> int:
    if not argv or argv[0] != "--" or len(argv) < 2:
        sys.stderr.write("usage: python -m enjrorun.backend.watchdog -- <command...>\n")
        return 2
    cmd = argv[1:]

    # Raw fd reads (not sys.stdin.buffer): a daemon thread blocked in a buffered read
    # aborts the interpreter at shutdown.
    stdin_fd = sys.stdin.fileno()
    first = b""
    while not first.endswith(b"\n"):
        chunk = os.read(stdin_fd, 1)
        if not chunk:
            return 0  # parent went away before starting us
        first += chunk
    if first.strip() != b"go":
        return 0

    # llama-server is a console program; without this it opens a window on Windows.
    kwargs: dict = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    if sys.platform.startswith("linux"):
        kwargs["preexec_fn"] = _linux_pdeathsig
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, **kwargs)
    except OSError as e:
        sys.stderr.write(f"watchdog: failed to start {cmd[0]}: {e}\n")
        return 127

    def watch_stdin() -> None:
        try:
            while os.read(stdin_fd, 4096):
                pass
        except OSError:
            pass
        _terminate(proc)

    threading.Thread(target=watch_stdin, daemon=True).start()

    if os.name != "nt":
        signal.signal(signal.SIGTERM, lambda *_: _terminate(proc))

    code = proc.wait()
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    code = main(sys.argv[1:])
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)  # skip interpreter finalization while the stdin thread may be blocked
