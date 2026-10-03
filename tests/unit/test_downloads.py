import asyncio
import itertools

import pytest

from strixrun.downloads import DownloadError, download_file


async def test_fake_worker_reports_increasing_progress(tmp_path):
    seen = []
    path = await download_file("r", "f", str(tmp_path), lambda d, t: seen.append((d, t)),
                               asyncio.Event(), fake_size=1000)
    assert path == "fake"
    assert len(seen) >= 5
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)
    assert seen[-1] == (1000, 1000)


async def test_cancel_stops_worker(tmp_path):
    cancel = asyncio.Event()
    seen = []

    def progress(done, total):
        seen.append(done)
        if len(seen) == 2:
            cancel.set()

    with pytest.raises(DownloadError, match="cancelled"):
        await download_file("r", "f", str(tmp_path), progress, cancel, fake_size=10_000)
    assert len(seen) < 20


@pytest.mark.network
async def test_real_xet_download_progress(tmp_path):
    seen = []
    path = await download_file("nomic-ai/nomic-embed-text-v1.5-GGUF", "nomic-embed-text-v1.5.Q4_K_M.gguf",
                               str(tmp_path), lambda d, t: seen.append(d), asyncio.Event())
    assert path.endswith(".gguf")
    increasing = [b for a, b in itertools.pairwise(seen) if b > a]
    assert len(increasing) >= 5


@pytest.mark.network
async def test_real_cancel_mid_download(tmp_path):
    cancel = asyncio.Event()

    def progress(done, total):
        if done > 10_000_000:
            cancel.set()

    with pytest.raises(DownloadError, match="cancelled"):
        await download_file("Qwen/Qwen2.5-0.5B-Instruct-GGUF", "qwen2.5-0.5b-instruct-q8_0.gguf",
                            str(tmp_path), progress, cancel)
