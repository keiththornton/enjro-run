"""One Enjro Run per data folder: an OS-level lock held for the server's lifetime.

Two servers sharing ~/.enjro/run would reap each other's llama-server children
(they share PID files), start duplicate models and overwrite state.json. The lock
is released by the OS if the process dies, so it can never go stale.
"""

import os
from pathlib import Path

# Windows locks a byte range, and a locked byte can't be read by anyone else. The lock
# sits past the PID so `stop` and the "already running" message can still read it.
LOCK_OFFSET = 4096


class AlreadyRunning(RuntimeError):
    pass


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.name == "nt":
                import msvcrt

                # StrixRun 0.1.x locked byte 0; a running one must still count.
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                other = os.read(fd, 32).decode(errors="replace").strip() or "unknown"
            except OSError:
                other = "unknown"
            os.close(fd)
            raise AlreadyRunning(
                f"Enjro Run is already running for {self.path.parent} (pid {other}). "
                "Stop it first, or set ENJRO_HOME to use a separate data folder."
            ) from None
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, str(os.getpid()).encode())
        os.lseek(fd, 0, os.SEEK_SET)
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(self._fd, LOCK_OFFSET, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None
