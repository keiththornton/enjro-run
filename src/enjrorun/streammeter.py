"""Pure parsing of OpenAI-style responses into request metrics.

StreamMeter is fed raw SSE bytes as they pass through the proxy, split at arbitrary
boundaries. It never changes the bytes. TTFT is measured from meter creation to the
first chunk carrying content, reasoning or a tool-call delta (a role-only first
chunk doesn't count).
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class MeterResult:
    ttft_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str | None = None
    timings: dict = field(default_factory=dict)
    tok_per_s: float | None = None
    error: str | None = None
    done: bool = False


def _has_output(delta: dict) -> bool:
    return bool(delta.get("content") or delta.get("reasoning_content") or delta.get("tool_calls"))


class StreamMeter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._start = clock()
        self._first_at: float | None = None
        self._last_at: float | None = None
        self._buf = b""
        self._r = MeterResult()

    def feed(self, data: bytes) -> None:
        self._buf += data
        while True:
            idx = self._buf.find(b"\n")
            if idx < 0:
                return
            line, self._buf = self._buf[:idx].rstrip(b"\r"), self._buf[idx + 1 :]
            if line.startswith(b"data:"):
                self._event(line[5:].strip())

    def _event(self, payload: bytes) -> None:
        now = self._clock()
        self._last_at = now
        if payload == b"[DONE]":
            self._r.done = True
            return
        try:
            obj = json.loads(payload)
        except ValueError:
            return
        if not isinstance(obj, dict):
            return
        if "error" in obj:
            err = obj["error"]
            self._r.error = err.get("message") if isinstance(err, dict) else str(err)
        for choice in obj.get("choices") or []:
            if self._first_at is None and _has_output(choice.get("delta") or {}):
                self._first_at = now
                self._r.ttft_ms = round((now - self._start) * 1000, 3)
            if choice.get("finish_reason"):
                self._r.finish_reason = choice["finish_reason"]
        _apply_usage(self._r, obj)

    def result(self) -> MeterResult:
        r = self._r
        if r.tok_per_s is None and r.completion_tokens and self._first_at is not None:
            elapsed = (self._last_at or self._clock()) - self._first_at
            if elapsed > 0:
                r.tok_per_s = round(r.completion_tokens / elapsed, 3)
        return r


def _apply_usage(r: MeterResult, obj: dict) -> None:
    usage = obj.get("usage")
    if isinstance(usage, dict):
        r.prompt_tokens = usage.get("prompt_tokens", r.prompt_tokens)
        r.completion_tokens = usage.get("completion_tokens", r.completion_tokens)
    timings = obj.get("timings")
    if isinstance(timings, dict):
        r.timings = timings
        if timings.get("predicted_per_second"):
            r.tok_per_s = float(timings["predicted_per_second"])


def meter_json(body: bytes) -> MeterResult:
    """Metrics from a non-streaming response body."""
    r = MeterResult(done=True)
    try:
        obj = json.loads(body)
    except ValueError:
        r.error = "unparseable response"
        return r
    if not isinstance(obj, dict):
        r.error = "unparseable response"
        return r
    if "error" in obj:
        err = obj["error"]
        r.error = err.get("message") if isinstance(err, dict) else str(err)
    choices = obj.get("choices") or []
    if choices:
        r.finish_reason = choices[0].get("finish_reason")
    _apply_usage(r, obj)
    return r
