"""Allowlisted passthrough to a child's native llama-server endpoints.

Only read-only, side-effect-free routes are exposed. Slot save/restore, LoRA loading
and everything else that touches files or server state stay unreachable.
"""

import time

import httpx
from fastapi import APIRouter, Request, Response

from strixrun import errors
from strixrun.api.deps import Ctx, request_meta
from strixrun.records import RequestRecord

router = APIRouter(prefix="/upstream")

ALLOWED = {
    ("GET", "health"),
    ("GET", "props"),
    ("GET", "slots"),
    ("POST", "tokenize"),
    ("POST", "detokenize"),
}


@router.api_route("/{model}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def upstream(model: str, path: str, request: Request, ctx: Ctx):
    if (request.method, path) not in ALLOWED:
        raise errors.NotFound(f"/upstream/{model}/{path}")
    entry = ctx.proxy.resolve(model)
    target = ctx.backend.target(entry.id)
    meta = request_meta(request)
    record = RequestRecord(
        model_id=entry.id, alias_used=model, endpoint="upstream", stream=False,
        client_ip=meta.client_ip, key_id=meta.key_id, request_body={"path": path},
    )
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.request(
                request.method, f"{target.base_url}/{path}", headers=target.headers,
                content=await request.body() if request.method == "POST" else None,
            )
    except httpx.HTTPError as e:
        record.status, record.http_status, record.error = "error", 502, type(e).__name__
        raise errors.UpstreamError(model, f"could not reach llama-server: {type(e).__name__}") from e
    finally:
        record.total_ms = round((time.monotonic() - started) * 1000, 3)
        if record.error:
            ctx.bus.publish("request.completed", record)
    record.http_status = r.status_code
    if r.status_code >= 400:
        record.status, record.error = "error", f"HTTP {r.status_code}"
    ctx.bus.publish("request.completed", record)
    return Response(r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type", "application/json"))
