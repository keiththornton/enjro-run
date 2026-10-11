import datetime as dt

import pytest

from enjrorun import errors
from enjrorun.discover import Discover, kind_of, params_from_name
from tests.unit.test_memory import FakeProbe

GiB = 1024**3
NOW = dt.datetime(2026, 10, 3, tzinfo=dt.UTC)


def repo(rid, tag="text-generation", downloads=100, likes=5, trending=10, days_old=100, gated=False):
    return {"repo": rid, "pipeline_tag": tag, "downloads": downloads, "likes": likes,
            "trending_score": trending, "created_at": (NOW - dt.timedelta(days=days_old)).isoformat(),
            "last_modified": NOW.isoformat(), "gated": gated}


class FakeHub:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def list_gguf(self, sort, limit):
        self.calls.append((sort, limit))
        key = {"trending": "trending_score", "downloads": "downloads", "likes": "likes"}[sort]
        return sorted(self.rows, key=lambda r: -r[key])[:limit]


ROWS = [
    repo("unsloth/Qwen3.8-27B-GGUF", tag=None, downloads=6_000_000, likes=4800, trending=120),
    repo("abenzerps/Qwen-Image-2.1-GGUF", tag="text-to-image", downloads=1_400_000, trending=680),
    repo("mudler/locate-anything.cpp-gguf", tag="object-detection", downloads=9_000_000),
    repo("bartowski/Llama-3.2-3B-Instruct-GGUF", downloads=900_000, trending=50),
    repo("nomic-ai/nomic-embed-text-v1.5-GGUF", tag="sentence-similarity", downloads=300_000, trending=5),
    repo("someone/Gemma4-26B-A4B-it-GGUF", tag="image-text-to-text", trending=300, days_old=6),
    repo("new/Tiny-1.5B-GGUF", trending=40, days_old=3),
]


def make(rows=ROWS, available=10 * GiB):
    from enjrorun.memory import MemoryEstimator

    return Discover(hub=FakeHub(rows), estimator=MemoryEstimator(FakeProbe(available=available)), now=lambda: NOW)


@pytest.mark.parametrize("name,expected", [
    ("unsloth/Qwen3.8-27B-GGUF", 27.0), ("Qwen3.5-35B-A3B-GGUF", 35.0), ("Llama-3.2-3B-Instruct", 3.0),
    ("Qwen2.5-0.5B-Instruct-GGUF", 0.5), ("Mixtral-8x7B-v0.1-GGUF", 56.0), ("gemma-270m-it-GGUF", 0.27),
    ("nomic-embed-text-v1.5-GGUF", None),
])
def test_params_from_name(name, expected):
    assert params_from_name(name) == expected


def test_kind_of():
    assert kind_of("text-generation", "x") == "chat"
    assert kind_of(None, "x-Instruct") == "chat"
    assert kind_of("sentence-similarity", "nomic-embed") == "embedding"
    assert kind_of(None, "bge-m3-embedding-GGUF") == "embedding"
    assert kind_of("image-text-to-text", "x") == "vision-chat"
    assert kind_of("text-to-image", "x") is None


async def test_only_runnable_models_are_listed():
    data = await make().list("trending")
    ids = [r["repo"] for r in data]
    assert "abenzerps/Qwen-Image-2.1-GGUF" not in ids and "mudler/locate-anything.cpp-gguf" not in ids
    assert ids[0] == "someone/Gemma4-26B-A4B-it-GGUF"  # highest trending among runnable ones
    assert {r["kind"] for r in data} == {"chat", "embedding", "vision-chat"}


async def test_sorts_and_kind_filter():
    d = make()
    assert (await d.list("downloads"))[0]["repo"] == "unsloth/Qwen3.8-27B-GGUF"
    assert (await d.list("likes"))[0]["repo"] == "unsloth/Qwen3.8-27B-GGUF"
    emb = await d.list("trending", kind="embedding")
    assert [r["repo"] for r in emb] == ["nomic-ai/nomic-embed-text-v1.5-GGUF"]


async def test_new_means_recent_and_rising():
    data = await make().list("new")
    assert [r["repo"] for r in data] == ["someone/Gemma4-26B-A4B-it-GGUF", "new/Tiny-1.5B-GGUF"]


async def test_fit_hint_uses_machine_memory():
    data = {r["repo"]: r for r in await make(available=10 * GiB).list("downloads")}
    assert data["bartowski/Llama-3.2-3B-Instruct-GGUF"]["fit_hint"] == "likely"
    assert data["unsloth/Qwen3.8-27B-GGUF"]["fit_hint"] == "unlikely"
    assert data["nomic-ai/nomic-embed-text-v1.5-GGUF"]["fit_hint"] is None  # size unknown from the name
    assert data["unsloth/Qwen3.8-27B-GGUF"]["params_b"] == 27.0


async def test_fits_only_filter():
    data = await make(available=10 * GiB).list("downloads", fits_only=True)
    assert all(r["fit_hint"] in ("likely", "tight", None) for r in data)
    assert "unsloth/Qwen3.8-27B-GGUF" not in [r["repo"] for r in data]


async def test_cached():
    d = make()
    await d.list("trending")
    await d.list("trending", kind="chat")
    assert len(d.hub.calls) == 1


async def test_bad_sort():
    with pytest.raises(errors.BadRequest):
        await make().list("random")
