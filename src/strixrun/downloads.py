"""Hugging Face downloads (search and jobs are added in step 22)."""

import asyncio
import json
import sys
from collections.abc import Callable

from strixrun.backend import procutil


class DownloadError(RuntimeError):
    pass


async def download_file(repo: str, filename: str, local_dir: str,
                        on_progress: Callable[[int, int | None], None],
                        cancel: asyncio.Event, fake_size: int | None = None) -> str:
    """Download one file via a watchdog-wrapped worker process. Raises DownloadError."""
    cmd = [sys.executable, "-m", "strixrun.hf_worker", repo, filename, local_dir]
    if fake_size is not None:
        cmd += ["--fake-size", str(fake_size)]
    proc = await procutil.spawn(cmd)
    cancel_wait = asyncio.create_task(cancel.wait())
    result: dict = {}
    try:
        while True:
            read = asyncio.create_task(proc.stdout.readline())
            done, _ = await asyncio.wait({read, cancel_wait}, return_when=asyncio.FIRST_COMPLETED)
            if cancel_wait in done:
                read.cancel()
                await procutil.stop(proc, grace=2)
                raise DownloadError("cancelled")
            line = read.result()
            if not line:
                break
            try:
                msg = json.loads(line)
            except ValueError:
                continue  # library warnings on stderr/stdout
            if "done" in msg:
                on_progress(int(msg["done"]), msg.get("total"))
            else:
                result = msg
    finally:
        cancel_wait.cancel()
        if proc.returncode is None:
            await procutil.stop(proc, grace=2)
    if "path" in result:
        return result["path"]
    raise DownloadError(result.get("error", "download worker exited without a result"))
