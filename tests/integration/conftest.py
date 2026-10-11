import asyncio
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn

from enjrorun.backend import procutil

FAKE = str(Path(__file__).resolve().parents[1] / "fake_llama_server.py")


@asynccontextmanager
async def fake_server(api_key: str = "childkey", **env: str):
    """Run tests/fake_llama_server.py as a plain subprocess with the given FAKE_* env knobs."""
    port = procutil.free_port()
    proc = subprocess.Popen(
        [sys.executable, FAKE, "--port", str(port), "--api-key", api_key],
        env={**os.environ, **env},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 15
        async with httpx.AsyncClient() as c:
            while True:
                try:
                    if (await c.get(f"http://127.0.0.1:{port}/health")).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                if time.monotonic() > deadline or proc.poll() is not None:
                    raise RuntimeError("fake server did not start")
                await asyncio.sleep(0.05)
        yield f"http://127.0.0.1:{port}", api_key, proc
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()


@asynccontextmanager
async def serve_app(app):
    """Serve an ASGI app with a real uvicorn server (needed for true streaming/disconnects)."""
    port = procutil.free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            task.result()
        await asyncio.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task
