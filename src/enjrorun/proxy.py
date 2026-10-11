"""Forwards OpenAI requests to the right llama-server child, with metrics.

Responsibilities:
- resolve alias → model → backend state (404 / 409 / 503 errors)
- admission control: a per-model semaphore sized to the preset's `parallel`, plus a
  bounded wait queue (429 when full)
- forwarding with a connect timeout and an idle timeout between chunks
- streaming through a hand-written ASGI response, so slot release, upstream close
  and record publishing happen exactly once, even if the client disconnects before
  the first byte
"""

import asyncio
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from enjrorun import errors
from enjrorun.backend.base import ModelStatus
from enjrorun.events import EventBus
from enjrorun.records import RequestRecord
from enjrorun.registry import Registry
from enjrorun.streammeter import StreamMeter, meter_json

ENDPOINT_PATHS = {
    "chat": "/v1/chat/completions",
    "completions": "/v1/completions",
    "embeddings": "/v1/embeddings",
}
CONNECT_TIMEOUT_S = 5.0
MAX_RECORDED_BODY = 256 * 1024
STREAM_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


@dataclass
class RequestMeta:
    client_ip: str | None
    key_id: str | None


class _Gate:
    """Semaphore plus a bounded count of waiters."""

    def __init__(self, parallel: int, queue_max: int) -> None:
        self.sem = asyncio.Semaphore(parallel)
        self.queue_max = queue_max
        self.waiting = 0

    async def acquire(self, model: str) -> float:
        start = time.monotonic()
        if self.sem.locked():
            if self.waiting >= self.queue_max:
                raise errors.QueueFull(model)
            self.waiting += 1
            try:
                await self.sem.acquire()
            finally:
                self.waiting -= 1
        else:
            await self.sem.acquire()
        return (time.monotonic() - start) * 1000

    def release(self) -> None:
        self.sem.release()


class Proxy:
    def __init__(
        self,
        registry: Registry,
        backend,
        bus: EventBus,
        idle_timeout: Callable[[], float] = lambda: 120.0,
    ) -> None:
        self.registry = registry
        self.backend = backend
        self.bus = bus
        self._idle_timeout = idle_timeout
        self._gates: dict[tuple[str, float | None], _Gate] = {}
        self._client = httpx.AsyncClient(timeout=None)

    async def aclose(self) -> None:
        await self._client.aclose()

    def queue_depth(self, model_id: str) -> int:
        return sum(g.waiting for (mid, _), g in self._gates.items() if mid == model_id)

    def _gate(self, model_id: str) -> _Gate:
        state = self.backend.state(model_id)
        key = (model_id, state.ready_at)  # a reload gets a fresh gate sized to its preset
        gate = self._gates.get(key)
        if gate is None:
            for k in [k for k in self._gates if k[0] == model_id]:
                del self._gates[k]
            preset = self.backend.preset(model_id)
            gate = _Gate(preset.parallel, preset.queue_max)
            self._gates[key] = gate
        return gate

    def resolve(self, name: str):
        entry = self.registry.resolve(name)
        if entry is None:
            raise errors.ModelNotFound(name)
        state = self.backend.state(entry.id)
        if state.status is ModelStatus.LOADING:
            raise errors.ModelLoading(name)
        if state.status is ModelStatus.CRASHED:
            raise errors.ModelCrashed(name, state.reason or "unknown reason")
        if state.status is not ModelStatus.READY:
            raise errors.ModelNotLoaded(name)
        return entry

    async def handle(self, endpoint: str, body: dict, meta: RequestMeta) -> Response:
        if not isinstance(body, dict):
            raise errors.BadRequest("request body must be a JSON object")
        name = body.get("model")
        if not isinstance(name, str) or not name:
            raise errors.BadRequest("the 'model' field is required")
        entry = self.resolve(name)
        preset = self.backend.preset(entry.id)
        wants_embedding = endpoint == "embeddings"
        if preset is not None and (preset.mode == "embedding") != wants_embedding:
            kind = "an embedding" if preset.mode == "embedding" else "a chat"
            raise errors.BadRequest(
                f"model {name!r} is loaded as {kind} model; use "
                f"{'/v1/embeddings' if preset.mode == 'embedding' else '/v1/chat/completions'}"
            )

        stream = bool(body.get("stream")) and not wants_embedding
        record = RequestRecord(
            model_id=entry.id, alias_used=name, endpoint=endpoint, stream=stream,
            client_ip=meta.client_ip, key_id=meta.key_id, request_body=body,
        )
        gate = self._gate(entry.id)
        record.queue_ms = round(await gate.acquire(name), 3)

        upstream_body = dict(body)
        target = self.backend.target(entry.id)
        upstream_body["model"] = target.upstream_model_name
        client_wants_usage = False
        if stream:
            opts = dict(body.get("stream_options") or {})
            client_wants_usage = bool(opts.get("include_usage"))
            opts["include_usage"] = True
            upstream_body["stream_options"] = opts

        timeout = httpx.Timeout(
            connect=CONNECT_TIMEOUT_S, read=self._idle_timeout(), write=30.0, pool=CONNECT_TIMEOUT_S
        )
        request = self._client.build_request(
            "POST", target.base_url + ENDPOINT_PATHS[endpoint],
            json=upstream_body, headers=target.headers, timeout=timeout,
        )
        started = time.monotonic()
        try:
            upstream = await self._client.send(request, stream=True)
        except httpx.HTTPError as e:
            gate.release()
            record.status, record.http_status = "error", 502
            record.error = f"could not reach llama-server: {type(e).__name__}"
            record.total_ms = round((time.monotonic() - started) * 1000, 3)
            self._publish(record)
            raise errors.UpstreamError(name, record.error) from e

        if not stream or upstream.status_code != 200:
            try:
                content = await upstream.aread()
            except httpx.HTTPError as e:
                record.status, record.http_status = "error", 502
                record.error = f"llama-server failed mid-response: {type(e).__name__}"
                raise errors.UpstreamError(name, record.error) from e
            finally:
                await upstream.aclose()
                gate.release()
                record.total_ms = round((time.monotonic() - started) * 1000, 3)
                if record.error:
                    self._publish(record)
            m = meter_json(content)
            record.http_status = upstream.status_code
            record.prompt_tokens, record.completion_tokens = m.prompt_tokens, m.completion_tokens
            record.tok_per_s = m.tok_per_s
            if upstream.status_code != 200:
                record.status, record.error = "error", m.error or f"HTTP {upstream.status_code}"
            content = _rename_model(content, target.upstream_model_name, name)
            record.response_body = _decode_for_ring(content)
            self._publish(record)
            return Response(
                content, status_code=upstream.status_code,
                media_type=upstream.headers.get("content-type", "application/json"),
            )

        return _ProxiedStream(self, upstream, gate, record, started, client_wants_usage, name,
                              target.upstream_model_name)

    def _publish(self, record: RequestRecord) -> None:
        self.bus.publish("request.completed", record)


def _decode_for_ring(content: bytes):
    if len(content) > MAX_RECORDED_BODY:
        return {"truncated": True, "bytes": len(content)}
    try:
        return json.loads(content)
    except ValueError:
        return content.decode("utf-8", "replace")


def _rename_model(data: bytes, upstream_name: str, client_name: str) -> bytes:
    """Report the model under the name the client asked for, not the child's internal one."""
    if upstream_name == client_name:
        return data
    pattern = re.compile(rb'("model"\s*:\s*)' + re.escape(json.dumps(upstream_name).encode()))
    replacement = json.dumps(client_name).encode()
    return pattern.sub(lambda m: m.group(1) + replacement, data)


def _is_usage_only(event: bytes) -> bool:
    for line in event.split(b"\n"):
        line = line.strip()
        if line.startswith(b"data:"):
            try:
                obj = json.loads(line[5:])
            except ValueError:
                return False
            return isinstance(obj, dict) and obj.get("choices") == [] and "usage" in obj
    return False


def _error_event(message: str) -> bytes:
    payload = {"error": {"message": message, "type": "server_error", "code": "upstream_error"}}
    return f"data: {json.dumps(payload)}\n\n".encode()


class _ProxiedStream(Response):
    """ASGI response that pumps upstream SSE to the client and cleans up exactly once."""

    def __init__(self, proxy: Proxy, upstream: httpx.Response, gate: _Gate,
                 record: RequestRecord, started: float, client_wants_usage: bool, name: str,
                 upstream_name: str) -> None:
        self.proxy, self.upstream, self.gate = proxy, upstream, gate
        self.record, self.started = record, started
        self.client_wants_usage, self.name = client_wants_usage, name
        self.upstream_name = upstream_name
        self.meter = StreamMeter()
        self.status_code = 200
        self.background = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        record = self.record
        record.http_status = 200
        transcript = bytearray()
        try:
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream; charset=utf-8")]
                + [(k.lower().encode(), v.encode()) for k, v in STREAM_HEADERS.items()],
            })
            pump = asyncio.create_task(self._pump(send, transcript))
            watch = asyncio.create_task(_wait_disconnect(receive))
            done, pending = await asyncio.wait({pump, watch}, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if watch in done and pump not in done:
                record.status = "cancelled"
            elif pump in done and pump.exception() is not None:
                raise pump.exception()
        except (asyncio.CancelledError, OSError):
            record.status = "cancelled"
            raise
        finally:
            await self.upstream.aclose()
            self.gate.release()
            m = self.meter.result()
            record.ttft_ms, record.tok_per_s = m.ttft_ms, m.tok_per_s
            record.prompt_tokens, record.completion_tokens = m.prompt_tokens, m.completion_tokens
            record.total_ms = round((time.monotonic() - self.started) * 1000, 3)
            record.response_body = (
                transcript.decode("utf-8", "replace") if len(transcript) <= MAX_RECORDED_BODY
                else {"truncated": True, "bytes": len(transcript)}
            )
            self.proxy._publish(record)

    async def _pump(self, send: Send, transcript: bytearray) -> None:
        buf = b""
        try:
            async for chunk in self.upstream.aiter_raw():
                self.meter.feed(chunk)
                buf += chunk.replace(b"\r\n", b"\n")
                while (idx := buf.find(b"\n\n")) >= 0:
                    event, buf = buf[: idx + 2], buf[idx + 2 :]
                    if not self.client_wants_usage and _is_usage_only(event):
                        continue
                    event = _rename_model(event, self.upstream_name, self.name)
                    if len(transcript) <= MAX_RECORDED_BODY:
                        transcript += event
                    await send({"type": "http.response.body", "body": event, "more_body": True})
            if buf:
                await send({"type": "http.response.body", "body": buf, "more_body": True})
        except httpx.ReadTimeout:
            self._fail(f"llama-server sent nothing for {self.proxy._idle_timeout():g}s (idle timeout)")
            await send({"type": "http.response.body", "body": _error_event(self.record.error),
                        "more_body": True})
        except httpx.HTTPError as e:
            self._fail(f"llama-server stopped mid-stream: {type(e).__name__}")
            await send({"type": "http.response.body", "body": _error_event(self.record.error),
                        "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    def _fail(self, message: str) -> None:
        self.record.status, self.record.error = "error", message


async def _wait_disconnect(receive: Receive) -> None:
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return
