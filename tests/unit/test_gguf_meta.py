import struct
from pathlib import Path

import httpx
import pytest

from enjrorun.gguf_meta import GGUFError, NeedMore, parse_header, read_local_meta, read_remote_meta
from tests.fixtures.make_tiny_gguf import write_gguf

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tiny.gguf"


def test_local_fixture_metadata():
    m = read_local_meta(FIXTURE)
    assert m["architecture"] == "llama"
    assert m["name"] == "Tiny Test Model"
    assert m["parameters"] == "1M"
    assert m["context_length"] == 4096
    assert m["n_layer"] == 2
    assert m["n_embd"] == 64
    assert m["n_head"] == 4
    assert m["n_head_kv"] == 2
    assert m["head_dim_k"] == m["head_dim_v"] == 16  # 64 / 4
    assert m["has_chat_template"] is True
    assert m["embedding_only"] is False
    assert m["file_type"] == 15


def test_explicit_key_length_and_per_layer_kv_heads(tmp_path):
    p = write_gguf(tmp_path / "a.gguf", head_count_kv=[2, 8], key_length=128)
    m = read_local_meta(p)
    assert m["n_head_kv"] == 8  # max over layers: size the KV cache for the worst layer
    assert m["head_dim_k"] == 128


def test_sliding_window_recorded(tmp_path):
    m = read_local_meta(write_gguf(tmp_path / "g.gguf", arch="gemma3", sliding_window=1024))
    assert m["sliding_window"] == 1024


@pytest.mark.parametrize("arch,pooling,expected", [
    ("nomic-bert", 1, True),
    ("bert", None, True),
    ("qwen3", 3, True),     # Qwen3-Embedding: a decoder arch with a pooling type
    ("qwen3", None, False),
    ("llama", 0, False),    # pooling type NONE
])
def test_embedding_detection(tmp_path, arch, pooling, expected):
    m = read_local_meta(write_gguf(tmp_path / "e.gguf", arch=arch, pooling_type=pooling, chat_template=None))
    assert m["embedding_only"] is expected


def test_no_chat_template(tmp_path):
    assert read_local_meta(write_gguf(tmp_path / "n.gguf", chat_template=None))["has_chat_template"] is False


def test_split_count(tmp_path):
    assert read_local_meta(write_gguf(tmp_path / "s.gguf", split=(0, 3)))["split_count"] == 3


def test_need_more_on_truncated_header():
    data = FIXTURE.read_bytes()
    with pytest.raises(NeedMore):
        parse_header(data[:100])


def test_header_stop_before_tokens_needs_little_data():
    data = FIXTURE.read_bytes()
    meta = parse_header(data[:1000], stop_at_tokenizer=True)
    assert meta["n_layer"] == 2 and meta["has_chat_template"] is None  # unknown: we stopped early


def test_bad_magic():
    with pytest.raises(GGUFError, match="not a GGUF"):
        parse_header(b"XXXX" + b"\0" * 100)


def test_absurd_lengths_rejected():
    # magic, version 3, 0 tensors, 1 kv; key length = 2^40
    blob = b"GGUF" + struct.pack("<IQQ", 3, 0, 1) + struct.pack("<Q", 1 << 40)
    with pytest.raises(GGUFError):
        parse_header(blob + b"\0" * 64)


async def test_remote_read_uses_ranges(tmp_path):
    big = write_gguf(tmp_path / "big.gguf", n_tokens=200_000)  # ~3 MB of vocab after the arch keys
    data = big.read_bytes()
    served = []

    def handler(request: httpx.Request) -> httpx.Response:
        start, end = request.headers["range"].removeprefix("bytes=").split("-")
        chunk = data[int(start) : int(end) + 1]
        served.append(len(chunk))
        return httpx.Response(206, content=chunk)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        m = await read_remote_meta("https://example/model.gguf", c)
    assert m["n_layer"] == 2 and m["context_length"] == 4096
    assert sum(served) < 600_000
    assert len(data) > 2_000_000


async def test_remote_server_ignoring_range_is_capped(tmp_path):
    big = write_gguf(tmp_path / "big.gguf", n_tokens=200_000)
    data = big.read_bytes()
    sent = []

    async def body():
        for i in range(0, len(data), 65536):
            sent.append(i)
            yield data[i : i + 65536]

    def handler(request):
        return httpx.Response(200, content=body())  # ignores Range entirely

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        m = await read_remote_meta("https://example/model.gguf", c)
    assert m["n_layer"] == 2
    assert len(sent) * 65536 < len(data)  # stopped reading early


@pytest.mark.network
async def test_remote_real_hf_file():
    url = ("https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/"
           "qwen2.5-0.5b-instruct-q4_k_m.gguf")
    transferred = 0

    async def count(response: httpx.Response):
        nonlocal transferred
        await response.aread()
        transferred += len(response.content)

    async with httpx.AsyncClient(follow_redirects=True, timeout=30,
                                 event_hooks={"response": [count]}) as c:
        m = await read_remote_meta(url, c)
    assert m["architecture"] == "qwen2"
    assert m["n_layer"] == 24 and m["n_head_kv"] == 2
    assert transferred < 2_000_000


def test_hybrid_layout_keys(tmp_path):
    import gguf

    p = tmp_path / "h.gguf"
    w = gguf.GGUFWriter(str(p), "qwen35")
    w.add_block_count(32)
    w.add_embedding_length(4096)
    w.add_head_count(16)
    w.add_head_count_kv(4)
    w.add_uint32("qwen35.full_attention_interval", 4)
    w.add_uint32("qwen35.ssm.state_size", 128)
    w.add_uint32("qwen35.ssm.inner_size", 4096)
    w.add_tensor("t", __import__("numpy").zeros((2, 2), dtype="float32"))
    w.write_header_to_file(); w.write_kv_data_to_file(); w.write_tensors_to_file(); w.close()
    m = read_local_meta(p)
    assert (m["full_attention_interval"], m["ssm_inner_size"], m["n_attn_layers"]) == (4, 4096, None)


def test_per_layer_kv_heads_counts_attention_layers(tmp_path):
    m = read_local_meta(write_gguf(tmp_path / "a.gguf", head_count_kv=[0, 8, 0, 0], block_count=4))
    assert m["n_attn_layers"] == 1 and m["n_head_kv"] == 8
