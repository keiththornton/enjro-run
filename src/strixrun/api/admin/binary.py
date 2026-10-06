"""llama-server binary: status, install/upgrade, rollback, progress events."""

import asyncio

from fastapi import APIRouter, Request
from pydantic import BaseModel

from strixrun import errors
from strixrun.api.deps import Ctx
from strixrun.api.sse import sse_response
from strixrun.binaries.manager import BinaryError

router = APIRouter()


class InstallIn(BaseModel):
    tag: str | None = None
    # A different build for this machine, e.g. the CUDA build StrixRun now detects in
    # place of the CPU build installed before. Only the detected variant is accepted.
    variant: str | None = None


@router.get("/binary")
def binary_status(ctx: Ctx):
    return ctx.binaries.status()


@router.get("/binary/events")
def binary_events(request: Request, ctx: Ctx):
    return sse_response(request, ctx.bus, {"binary.progress"})


@router.post("/binary/install", status_code=202)
async def install_binary(body: InstallIn, ctx: Ctx):
    tag = body.tag or ctx.binaries.manifest.default
    if tag not in ctx.binaries.manifest.tags():
        raise errors.BadRequest(f"llama.cpp {tag} is not in the pinned manifest")
    if ctx.binaries.installing:
        raise errors.Conflict(f"an install of {ctx.binaries.installing} is already running")
    if body.variant is not None and body.variant != ctx.binaries.status()["detected_variant"]:
        raise errors.BadRequest(f"{body.variant} isn't the build detected for this machine")
    if body.variant is not None and ctx.backend.loaded():
        raise errors.Conflict("unload every model before switching llama.cpp builds")
    task = asyncio.create_task(ctx.binaries.install(tag, body.variant))
    task.add_done_callback(lambda t: t.exception())  # errors are reported via status/events
    await asyncio.sleep(0)
    return ctx.binaries.status()


@router.post("/binary/rollback")
async def rollback_binary(ctx: Ctx):
    try:
        return await ctx.binaries.rollback()
    except BinaryError as e:
        raise errors.Conflict(str(e)) from e
