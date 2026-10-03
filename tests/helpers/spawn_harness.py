"""Spawns a fake llama-server via procutil, writes READY to stdout, then sleeps.

The test kills this harness abruptly and checks its children don't survive.
"""

import asyncio
import sys
from pathlib import Path

from strixrun.backend import procutil

FAKE = str(Path(__file__).resolve().parents[1] / "fake_llama_server.py")


async def main() -> None:
    port = int(sys.argv[1])
    await procutil.spawn([sys.executable, FAKE, "--port", str(port)])
    print("READY", flush=True)
    await asyncio.sleep(3600)


asyncio.run(main())
