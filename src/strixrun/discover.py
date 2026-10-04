"""Discover GGUF models on Hugging Face: trending, most downloaded, most liked, new.

Only repos StrixRun can actually run are listed: chat, chat-with-vision (the text
part) and embedding models. Image generators, detectors and speech models are
dropped. A rough "fits at Q4" hint uses the parameter count from the repo name;
the exact per-quant badges come from the repo listing when it's expanded.
"""

import asyncio
import datetime as dt
import re
import time
from collections.abc import Callable

from strixrun import errors
from strixrun.presets import Preset

CACHE_TTL_S = 600
NEW_WINDOW_DAYS = 30
FETCH = {"trending": 150, "downloads": 150, "likes": 150, "new": 300}
Q4_BYTES_PER_PARAM = 0.6  # Q4_K_M is ~4.8 bits per weight
OVERHEAD_BYTES = 1024**3  # KV cache at a default context plus compute buffers

CHAT_TAGS = {"text-generation", "text2text-generation", "conversational"}
VISION_TAGS = {"image-text-to-text", "visual-question-answering"}
EMBED_TAGS = {"feature-extraction", "sentence-similarity"}
EMBED_NAME = re.compile(r"embed|(?<![a-z])(bge|e5|gte)(?![a-z])", re.IGNORECASE)
MOE_RE = re.compile(r"(?<![A-Za-z0-9.])(\d+)x(\d+(?:\.\d+)?)[Bb](?![A-Za-z])")
SIZE_RE = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)([BbMm])(?![A-Za-z])")


def params_from_name(name: str) -> float | None:
    """Total parameters in billions, from names like 27B, 35B-A3B, 8x7B, 270m."""
    base = name.split("/")[-1]
    moe = MOE_RE.search(base)
    if moe:
        return float(moe[1]) * float(moe[2])
    sizes = [float(n) / (1000 if unit in "Mm" else 1) for n, unit in SIZE_RE.findall(base)]
    return max(sizes) if sizes else None


def kind_of(tag: str | None, name: str) -> str | None:
    if tag in CHAT_TAGS:
        return "chat"
    if tag in VISION_TAGS:
        return "vision-chat"
    if tag in EMBED_TAGS:
        return "embedding"
    if tag is None:
        return "embedding" if EMBED_NAME.search(name) else "chat"
    return None  # image generation, detection, speech…: not runnable here


class Discover:
    def __init__(self, hub, estimator, now: Callable[[], dt.datetime] | None = None) -> None:
        self.hub = hub
        self.estimator = estimator
        self._now = now or (lambda: dt.datetime.now(dt.UTC))
        self._cache: dict[str, tuple[float, int, list[dict]]] = {}

    async def _fetch(self, hub_sort: str, limit: int) -> list[dict]:
        hit = self._cache.get(hub_sort)
        if hit and time.monotonic() - hit[0] < CACHE_TTL_S and hit[1] >= limit:
            return hit[2]
        try:
            rows = await asyncio.to_thread(self.hub.list_gguf, hub_sort, limit)
        except Exception as e:  # mapped to a user-facing error
            from strixrun.downloads import hub_error

            raise hub_error(e, "Hugging Face") from e
        self._cache[hub_sort] = (time.monotonic(), limit, rows)
        return rows

    def _fit_hint(self, params_b: float | None) -> str | None:
        if params_b is None or self.estimator is None:
            return None
        need = params_b * 1e9 * Q4_BYTES_PER_PARAM + OVERHEAD_BYTES
        verdict = self.estimator.place(int(need), Preset())["verdict"]
        return {"ok": "likely", "tight": "tight", "over": "unlikely"}[verdict]

    async def list(self, sort: str, kind: str = "all", fits_only: bool = False, limit: int = 40) -> list[dict]:
        if sort not in FETCH:
            raise errors.BadRequest(f"sort must be one of {', '.join(FETCH)}")
        if kind not in ("all", "chat", "embedding", "vision-chat"):
            raise errors.BadRequest("kind must be all, chat, embedding or vision-chat")
        rows = await self._fetch("trending" if sort == "new" else sort, FETCH[sort])
        cutoff = self._now() - dt.timedelta(days=NEW_WINDOW_DAYS)
        out = []
        for r in rows:
            k = kind_of(r.get("pipeline_tag"), r["repo"])
            if k is None or (kind != "all" and k != kind):
                continue
            if sort == "new":
                created = r.get("created_at")
                if not created or dt.datetime.fromisoformat(created) < cutoff:
                    continue
            params = params_from_name(r["repo"]) if k != "embedding" or EMBED_NAME.search(r["repo"]) is None else None
            hint = self._fit_hint(params)
            if fits_only and hint == "unlikely":
                continue
            out.append({**r, "kind": k, "params_b": params, "fit_hint": hint})
            if len(out) >= limit:
                break
        return out
