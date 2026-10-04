import asyncio
import itertools
from pathlib import Path

import pytest

from strixrun import errors
from strixrun.downloads import DownloadError, Downloads, download_file
from strixrun.events import EventBus
from strixrun.memory import MemoryEstimator
from strixrun.registry import Registry, quant_of
from tests.fixtures.make_tiny_gguf import write_gguf
from tests.unit.test_memory import FakeProbe


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


# ---- search / listing / jobs (fake hub) -------------------------------------------------



GiB = 1024**3
META = {"architecture": "qwen2", "n_layer": 28, "n_head": 12, "n_head_kv": 2, "head_dim_k": 128,
        "head_dim_v": 128, "context_length": 32768}


class FakeHub:
    def __init__(self, files, gated=False, fail=None):
        self.files, self.gated, self.fail = files, gated, fail
        self.calls = {"repo": 0, "meta": 0}

    def search(self, q, limit):
        if self.fail:
            raise self.fail
        return [{"repo": "owner/Model-GGUF", "downloads": 10}]

    def repo_files(self, repo):
        self.calls["repo"] += 1
        if self.fail:
            raise self.fail
        return {"gated": self.gated, "files": self.files}

    async def remote_meta(self, repo, filename):
        self.calls["meta"] += 1
        return META


FILES = [
    ("README.md", 100),
    ("model-Q4_K_M.gguf", int(1.1 * GiB)),
    ("model-Q8_0.gguf", int(1.9 * GiB)),
    ("big/model-F16-00001-of-00002.gguf", 4 * GiB),
    ("big/model-F16-00002-of-00002.gguf", 3 * GiB),
    ("mmproj-model-f16.gguf", 600_000_000),
]


def make(tmp_path, hub, fetch=None, available=10 * GiB):
    reg = Registry(tmp_path / "models.json")
    store = tmp_path / "store"
    d = Downloads(EventBus(), reg, lambda: store, estimator=MemoryEstimator(FakeProbe(available=available)),
                  hub=hub, fetch=fetch or fake_fetch)
    return d, reg, store


async def fake_fetch(repo, filename, local_dir, on_progress, cancel, fake_size=None):
    dest = Path(local_dir) / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    write_gguf(dest)
    for i in range(1, 4):
        on_progress(i * 100, 300)
        await asyncio.sleep(0)
    return str(dest)


@pytest.mark.parametrize("name,quant", [
    ("qwen2.5-7b-instruct-q4_k_m.gguf", "Q4_K_M"), ("Model.IQ4_XS.gguf", "IQ4_XS"),
    ("m-Q8_0-00001-of-00002.gguf", "Q8_0"), ("m-bf16.gguf", "BF16"), ("gpt-oss-20b-MXFP4.gguf", "MXFP4"),
    ("readme.gguf", None),
])
def test_quant_of(name, quant):
    assert quant_of(name) == quant


async def test_listing_groups_quants_merges_shards_and_badges(tmp_path):
    d, _, _ = make(tmp_path, FakeHub(FILES), available=8 * GiB)
    listing = await d.repo_listing("owner/Model-GGUF")
    by = {q["quant"]: q for q in listing["quants"]}
    assert set(by) == {"Q4_K_M", "Q8_0", "F16"}  # mmproj and README excluded
    assert by["F16"]["files"] == ["big/model-F16-00001-of-00002.gguf", "big/model-F16-00002-of-00002.gguf"]
    assert by["F16"]["size_bytes"] == 7 * GiB
    assert by["Q4_K_M"]["fit"]["verdict"] == "ok"
    assert by["F16"]["fit"]["verdict"] == "over"
    assert by["Q4_K_M"]["model_id"] == "owner--model--q4_k_m"
    assert listing["meta"]["architecture"] == "qwen2"


async def test_listing_is_cached(tmp_path):
    hub = FakeHub(FILES)
    d, _, _ = make(tmp_path, hub)
    await d.repo_listing("owner/Model-GGUF")
    await d.repo_listing("owner/Model-GGUF")
    assert hub.calls == {"repo": 1, "meta": 1}


async def test_download_registers_model_and_emits_progress(tmp_path):
    d, reg, store = make(tmp_path, FakeHub(FILES))
    sub = d.bus.subscribe({"download.progress"})
    job = await d.start("owner/Model-GGUF", "F16")
    await asyncio.wait_for(asyncio.gather(*d._tasks), 10)
    assert job.status == "done", job.error
    entry = reg.get(job.model_id)
    assert entry is not None and len(entry.paths) == 2
    assert entry.meta["architecture"] == "llama"  # parsed from the downloaded file
    assert entry.aliases == ["model:f16"]
    assert all(Path(p).resolve().is_relative_to(store.resolve()) for p in entry.paths)
    statuses = []
    while not sub.empty():
        statuses.append((await sub.get()).data["status"])
    assert statuses[0] == "queued" and statuses[-1] == "done"


async def test_unknown_quant_and_duplicates(tmp_path):
    d, _, _ = make(tmp_path, FakeHub(FILES))
    with pytest.raises(errors.BadRequest, match="no Q2_K"):
        await d.start("owner/Model-GGUF", "Q2_K")
    await d.start("owner/Model-GGUF", "Q4_K_M")
    with pytest.raises(errors.Conflict):
        await d.start("owner/Model-GGUF", "Q4_K_M")
    await asyncio.gather(*d._tasks)
    with pytest.raises(errors.Conflict, match="already downloaded"):
        await d.start("owner/Model-GGUF", "Q4_K_M")


@pytest.mark.parametrize("bad", ["../evil-Q4_K_M.gguf", "/abs/evil-Q4_K_M.gguf", "a/../../evil-Q4_K_M.gguf"])
async def test_traversal_in_listing_rejected(tmp_path, bad):
    d, _, _ = make(tmp_path, FakeHub([(bad, 10)]))
    with pytest.raises(errors.BadRequest):
        await d.start("owner/Model-GGUF", "Q4_K_M")


async def test_invalid_repo_name(tmp_path):
    d, _, _ = make(tmp_path, FakeHub(FILES))
    with pytest.raises(errors.BadRequest):
        await d.start("../../etc", "Q4_K_M")


async def test_disk_precheck(tmp_path, monkeypatch):
    from collections import namedtuple

    d, _, _ = make(tmp_path, FakeHub(FILES))
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr("strixrun.downloads.shutil.disk_usage", lambda p: usage(1, 1, 100))
    with pytest.raises(errors.StrixError) as e:
        await d.start("owner/Model-GGUF", "Q4_K_M")
    assert e.value.status == 507


async def test_cancel(tmp_path):
    async def slow_fetch(repo, filename, local_dir, on_progress, cancel, fake_size=None):
        await cancel.wait()
        from strixrun.downloads import DownloadError as DE

        raise DE("cancelled")

    d, reg, _ = make(tmp_path, FakeHub(FILES), fetch=slow_fetch)
    job = await d.start("owner/Model-GGUF", "Q4_K_M")
    await asyncio.sleep(0.05)
    d.cancel(job.id)
    await asyncio.wait_for(asyncio.gather(*d._tasks), 5)
    assert job.status == "cancelled" and reg.get(job.model_id) is None


async def test_gated_and_rate_limit_errors(tmp_path):
    class GatedRepoError(Exception):
        pass

    d, _, _ = make(tmp_path, FakeHub(FILES, fail=GatedRepoError("Access to model is restricted")))
    with pytest.raises(errors.StrixError) as e:
        await d.repo_listing("owner/Gated-GGUF")
    assert e.value.status == 403 and "huggingface.co/owner/Gated-GGUF" in e.value.message

    d, _, _ = make(tmp_path, FakeHub(FILES, fail=RuntimeError("429 Client Error: Too Many Requests")))
    with pytest.raises(errors.StrixError) as e:
        await d.search("qwen")
    assert e.value.status == 429 and "token" in e.value.message


# ---- damaged uploads ------------------------------------------------------------------


class DamagedHub(FakeHub):
    """Like FakeHub, but one file's header on 'Hugging Face' is not a GGUF."""

    def __init__(self, files, damaged: str, unreachable: bool = False):
        super().__init__(files)
        self.damaged, self.unreachable = damaged, unreachable

    async def remote_meta(self, repo, filename):
        from strixrun.gguf_meta import GGUFError

        if self.unreachable:
            raise OSError("network down")
        if filename == self.damaged:
            raise GGUFError("not a GGUF file")
        return META


async def test_damaged_upload_is_refused_before_downloading(tmp_path):
    fetched = []

    async def fetch(*a, **kw):
        fetched.append(a)
        raise AssertionError("must not download")

    d, _, _ = make(tmp_path, DamagedHub(FILES, damaged="model-Q8_0.gguf"), fetch=fetch)
    with pytest.raises(errors.StrixError) as e:
        await d.start("owner/Model-GGUF", "Q8_0")
    assert e.value.status == 422 and e.value.code == "damaged_gguf"
    assert "model-Q8_0.gguf" in e.value.message and "another quant" in e.value.message
    assert not fetched and not d.jobs


async def test_header_check_failure_to_connect_does_not_block(tmp_path):
    d, _, _ = make(tmp_path, DamagedHub(FILES, damaged="", unreachable=True))
    job = await d.start("owner/Model-GGUF", "Q4_K_M")
    await asyncio.wait_for(asyncio.gather(*d._tasks), 10)
    assert job.status == "done"


async def test_bad_file_after_download_is_deleted_with_clear_error(tmp_path):
    async def junk_fetch(repo, filename, local_dir, on_progress, cancel, fake_size=None):
        dest = Path(local_dir) / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\0" * 4096)
        return str(dest)

    d, reg, _ = make(tmp_path, FakeHub(FILES), fetch=junk_fetch)
    job = await d.start("owner/Model-GGUF", "Q4_K_M")
    await asyncio.wait_for(asyncio.gather(*d._tasks), 10)
    assert job.status == "error"
    assert "isn't a valid GGUF" in job.error and "another quant" in job.error
    assert not (Path(job.target_dir) / "model-Q4_K_M.gguf").exists()
    assert reg.get(job.model_id) is None
