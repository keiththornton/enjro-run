"""GGUF header parsing for local files and remote (HTTP range) reads.

Only the key/value metadata is read; tensors are never touched. In a converted model
the architecture keys come before the (often multi-megabyte) tokenizer vocabulary,
so a remote read can stop at `tokenizer.ggml.tokens` after a few hundred KB.
"""

import struct
from pathlib import Path

import httpx

MAGIC = b"GGUF"
MAX_STRING = 64 * 1024 * 1024
MAX_ARRAY = 64 * 1024 * 1024
MAX_KV = 1_000_000
REMOTE_FIRST = 256 * 1024
REMOTE_MAX = 32 * 1024 * 1024
EMBEDDING_ARCHS = {
    "bert", "nomic-bert", "nomic-bert-moe", "jina-bert-v2", "jina-bert-v3",
    "neo-bert", "modern-bert", "t5encoder",
}

# type id -> struct format (scalars)
SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?",
           10: "<Q", 11: "<q", 12: "<d"}
T_STRING, T_ARRAY = 8, 9


class GGUFError(ValueError):
    pass


class NeedMore(Exception):  # a control-flow signal, not an error
    """The buffer ended before the header did."""


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data, self.pos = data, 0

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise NeedMore
        b = self.data[self.pos : self.pos + n]
        self.pos += n
        return b

    def unpack(self, fmt: str):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def string(self) -> str:
        n = self.unpack("<Q")
        if n > MAX_STRING:
            raise GGUFError(f"string length {n} is implausible")
        return self.take(n).decode("utf-8", "replace")

    def skip_string(self) -> None:
        n = self.unpack("<Q")
        if n > MAX_STRING:
            raise GGUFError(f"string length {n} is implausible")
        self.take(n)

    def value(self, vtype: int, keep: bool):
        if vtype in SCALARS:
            return self.unpack(SCALARS[vtype])
        if vtype == T_STRING:
            if keep:
                return self.string()
            self.skip_string()
            return None
        if vtype == T_ARRAY:
            etype, count = self.unpack("<I"), self.unpack("<Q")
            if count > MAX_ARRAY:
                raise GGUFError(f"array length {count} is implausible")
            if etype in SCALARS and not keep:
                self.take(struct.calcsize(SCALARS[etype]) * count)
                return None
            items = [self.value(etype, keep) for _ in range(count)]
            return items if keep else None
        raise GGUFError(f"unknown GGUF value type {vtype}")


def _keep(key: str) -> bool:
    # Keep everything except big tokenizer tables and the template text itself.
    return not key.startswith("tokenizer.ggml.") and key != "tokenizer.chat_template"


def parse_kv(data: bytes, stop_at_tokenizer: bool = False) -> dict:
    """Raw metadata dict. Raises NeedMore if `data` ends inside the header."""
    r = _Reader(data)
    if r.take(4) != MAGIC:
        raise GGUFError("not a GGUF file")
    version = r.unpack("<I")
    if version not in (2, 3):
        raise GGUFError(f"unsupported GGUF version {version}")
    r.unpack("<Q")  # tensor count
    n_kv = r.unpack("<Q")
    if n_kv > MAX_KV:
        raise GGUFError(f"{n_kv} metadata entries is implausible")
    kv: dict = {}
    for _ in range(n_kv):
        key = r.string()
        if stop_at_tokenizer and key.startswith("tokenizer."):
            kv["__stopped__"] = True
            break
        vtype = r.unpack("<I")
        if key == "tokenizer.chat_template":
            r.value(vtype, keep=False)
            kv[key] = True
            continue
        value = r.value(vtype, keep=_keep(key))
        if value is not None:
            kv[key] = value
    return kv


def _max(v):
    if isinstance(v, list):
        return max(v) if v else None
    return v


def summarize(kv: dict) -> dict:
    arch = kv.get("general.architecture", "")

    def a(key):
        return kv.get(f"{arch}.{key}")

    n_embd = a("embedding_length")
    n_head = _max(a("attention.head_count"))
    n_head_kv = _max(a("attention.head_count_kv")) or n_head
    default_head_dim = n_embd // n_head if n_embd and n_head else None
    pooling = a("pooling_type")
    stopped = kv.get("__stopped__", False)
    return {
        "architecture": arch or None,
        "name": kv.get("general.name"),
        "parameters": kv.get("general.size_label"),
        "file_type": kv.get("general.file_type"),
        "context_length": a("context_length"),
        "n_layer": a("block_count"),
        "n_embd": n_embd,
        "n_head": n_head,
        "n_head_kv": n_head_kv,
        "head_dim_k": a("attention.key_length") or default_head_dim,
        "head_dim_v": a("attention.value_length") or default_head_dim,
        "sliding_window": a("attention.sliding_window"),
        "sliding_window_pattern": a("attention.sliding_window_pattern"),
        "ssm_state_size": a("ssm.state_size"),
        "embedding_only": arch in EMBEDDING_ARCHS or bool(pooling),
        "has_chat_template": None if stopped else bool(kv.get("tokenizer.chat_template")),
        "split_count": kv.get("split.count"),
    }


def parse_header(data: bytes, stop_at_tokenizer: bool = False) -> dict:
    return summarize(parse_kv(data, stop_at_tokenizer))


def read_local_meta(path: Path) -> dict:
    size = REMOTE_FIRST
    with open(path, "rb") as f:
        while True:
            f.seek(0)
            data = f.read(size)
            try:
                return parse_header(data)
            except NeedMore:
                if len(data) < size:
                    raise GGUFError("file ends inside the GGUF header") from None
                size *= 4


async def read_remote_meta(url: str, client: httpx.AsyncClient, headers: dict | None = None) -> dict:
    """Read just enough of a remote GGUF to summarize it, using HTTP range requests.

    Reads are streamed and capped, so a server that ignores Range (and answers 200
    with the whole multi-GB file) costs no more than the bytes we actually need.
    """
    data = b""
    want = REMOTE_FIRST
    while True:
        if want > len(data):
            data = await _read_range(client, url, headers or {}, data, want)
        try:
            return parse_header(data, stop_at_tokenizer=True)
        except NeedMore:
            if want >= REMOTE_MAX:
                raise GGUFError("GGUF header is larger than the remote read limit") from None
            want = min(want * 4, REMOTE_MAX)


async def _read_range(client: httpx.AsyncClient, url: str, headers: dict, have: bytes, want: int) -> bytes:
    req_headers = {**headers, "Range": f"bytes={len(have)}-{want - 1}"}
    async with client.stream("GET", url, headers=req_headers) as r:
        if r.status_code not in (200, 206):
            await r.aread()
            r.raise_for_status()
            raise GGUFError(f"unexpected HTTP {r.status_code}")
        buf = bytearray(have if r.status_code == 206 else b"")
        async for chunk in r.aiter_bytes():
            buf += chunk
            if len(buf) >= want:
                break
        return bytes(buf[:want])
