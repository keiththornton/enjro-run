"""OpenAI-compatible routes. Everything except /v1/models is proxied to a child."""

import json
import time

from fastapi import APIRouter, Request

from enjrorun import errors
from enjrorun.api.deps import Ctx, request_meta
from enjrorun.backend.base import ModelStatus

router = APIRouter(prefix="/v1")


async def _json_body(request: Request) -> dict:
    try:
        body = json.loads(await request.body())
    except ValueError as e:
        raise errors.BadRequest("request body must be valid JSON") from e
    if not isinstance(body, dict):
        raise errors.BadRequest("request body must be a JSON object")
    return body


@router.post("/chat/completions")
async def chat_completions(request: Request, ctx: Ctx):
    return await ctx.proxy.handle("chat", await _json_body(request), request_meta(request))


@router.post("/completions")
async def completions(request: Request, ctx: Ctx):
    return await ctx.proxy.handle("completions", await _json_body(request), request_meta(request))


@router.post("/embeddings")
async def embeddings(request: Request, ctx: Ctx):
    return await ctx.proxy.handle("embeddings", await _json_body(request), request_meta(request))


@router.get("/models")
def list_models(ctx: Ctx, all: int = 0):
    data = []
    for entry in ctx.registry.all():
        state = ctx.backend.state(entry.id)
        loaded = state.status is ModelStatus.READY
        if not loaded and not all:
            continue
        for alias in entry.aliases:
            item = {
                "id": alias,
                "object": "model",
                "created": int(entry.added_at or time.time()),
                "owned_by": entry.owner or "local",
            }
            if all:
                item["status"] = state.status.value
                item["enjrorun_id"] = entry.id
            data.append(item)
    return {"object": "list", "data": data}
