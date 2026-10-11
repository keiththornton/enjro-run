"""Model management: list, load/unload, aliases, delete, logs, live state events, ps."""

import os
import shutil
import time
from pathlib import Path

import psutil
from fastapi import APIRouter, Request
from pydantic import BaseModel

from enjrorun import errors
from enjrorun.api.deps import Ctx
from enjrorun.api.sse import sse_response
from enjrorun.backend.base import ModelStatus
from enjrorun.context import AppContext
from enjrorun.presets import Preset
from enjrorun.registry import ModelEntry

router = APIRouter()

META_FIELDS = ("architecture", "context_length", "embedding_only", "n_layer", "parameters")


def _entry(ctx: AppContext, model_id: str) -> ModelEntry:
    entry = ctx.registry.get(model_id)
    if entry is None:
        raise errors.NotFound(f"model {model_id}")
    return entry


def model_view(ctx: AppContext, entry: ModelEntry) -> dict:
    state = ctx.backend.state(entry.id)
    preset = ctx.backend.preset(entry.id) if state.status in (
        ModelStatus.LOADING, ModelStatus.READY) else None
    preset = preset or ctx.presets.load(entry.id, entry.meta)
    return {
        "id": entry.id,
        "aliases": entry.aliases,
        "repo": entry.repo,
        "quant": entry.quant,
        "size_bytes": entry.size_bytes,
        "location": entry.location,
        "files": len(entry.paths),
        "meta": {k: entry.meta.get(k) for k in META_FIELDS if k in entry.meta},
        "state": state.to_dict(),
        "preset": preset.to_dict(),
        "queue_depth": ctx.proxy.queue_depth(entry.id),
    }


@router.get("/models")
def list_models(ctx: Ctx):
    return {"data": [model_view(ctx, e) for e in ctx.registry.all()]}


@router.get("/models/events")
def model_events(request: Request, ctx: Ctx):
    return sse_response(request, ctx.bus, {"model.state", "model.log"})


@router.get("/models/{model_id}")
def get_model(model_id: str, ctx: Ctx):
    return model_view(ctx, _entry(ctx, model_id))


class AliasesIn(BaseModel):
    aliases: list[str]


@router.patch("/models/{model_id}")
def update_model(model_id: str, body: AliasesIn, ctx: Ctx):
    _entry(ctx, model_id)
    ctx.registry.set_aliases(model_id, body.aliases)
    return model_view(ctx, _entry(ctx, model_id))


class EstimateIn(BaseModel):
    preset: dict | None = None


@router.post("/models/{model_id}/estimate")
def estimate_model(model_id: str, body: EstimateIn, ctx: Ctx):
    entry = _entry(ctx, model_id)
    current = ctx.presets.load(entry.id, entry.meta).to_dict()
    preset = Preset.from_dict({**current, **(body.preset or {})})
    return {**ctx.estimator.estimate(entry, preset),
            "ctx_per_request": preset.ctx_per_request, "parallel": preset.parallel}


@router.post("/models/{model_id}/fit")
def fit_model(model_id: str, body: EstimateIn, ctx: Ctx):
    """Settings that keep every layer on the GPU: the largest context, f16 or q8_0 KV cache."""
    entry = _entry(ctx, model_id)
    current = ctx.presets.load(entry.id, entry.meta).to_dict()
    return ctx.estimator.fit_on_gpu(entry, Preset.from_dict({**current, **(body.preset or {})}))


class LoadIn(BaseModel):
    preset: dict | None = None
    confirm: bool = False


@router.post("/models/{model_id}/load", status_code=202)
async def load_model(model_id: str, body: LoadIn, ctx: Ctx):
    entry = _entry(ctx, model_id)
    current = ctx.presets.load(entry.id, entry.meta).to_dict()
    preset = Preset.from_dict({**current, **(body.preset or {})})
    if ctx.estimator is not None:
        estimate = ctx.estimator.estimate(entry, preset)
        if estimate["verdict"] != "ok" and not body.confirm:
            raise errors.NeedsConfirmation(estimate)
    await ctx.backend.load(entry, preset)
    ctx.presets.save(entry.id, preset)  # remember last-used settings
    return model_view(ctx, entry)


@router.post("/models/{model_id}/unload")
async def unload_model(model_id: str, ctx: Ctx):
    entry = _entry(ctx, model_id)
    await ctx.backend.unload(entry.id)
    return model_view(ctx, entry)


@router.get("/models/{model_id}/logs")
def model_logs(model_id: str, ctx: Ctx):
    _entry(ctx, model_id)
    return {"lines": ctx.backend.logs(model_id)}


@router.delete("/models/{model_id}")
async def delete_model(model_id: str, ctx: Ctx):
    entry = _entry(ctx, model_id)
    if ctx.backend.state(entry.id).status in (ModelStatus.LOADING, ModelStatus.READY,
                                              ModelStatus.STOPPING):
        raise errors.Conflict("unload the model before deleting it")
    if entry.location != "store":
        raise errors.Forbidden(
            "this model lives in a folder Enjro Run only reads from; delete it there yourself"
        )
    store = Path(ctx.settings.current.models_dirs[0]).resolve()
    files = [Path(p).resolve() for p in entry.paths]
    for f in files:
        if store not in f.parents:
            raise errors.Forbidden(f"{f} is outside the Enjro Run model folder; not deleting")
    for f in files:
        f.unlink(missing_ok=True)
    parent = files[0].parent if files else None
    if parent and parent != store and parent.is_dir() and not any(parent.iterdir()):
        shutil.rmtree(parent, ignore_errors=True)
    ctx.registry.remove(entry.id)
    ctx.presets.delete(entry.id)
    return {"deleted": entry.id}


def _tree_rss(pid: int | None) -> int | None:
    if not pid:
        return None
    try:
        root = psutil.Process(pid)
        return sum(p.memory_info().rss for p in [root, *root.children(recursive=True)])
    except psutil.Error:
        return None


@router.get("/ps")
def ps(ctx: Ctx):
    now = time.time()
    out = []
    for model_id in ctx.backend.loaded():
        entry = ctx.registry.get(model_id)
        state = ctx.backend.state(model_id)
        preset = ctx.backend.preset(model_id)
        out.append({
            "id": model_id,
            "aliases": entry.aliases if entry else [],
            "status": state.status.value,
            "pid": state.pid,
            "port": state.port,
            "uptime_s": round(now - state.ready_at, 1) if state.ready_at else None,
            "rss_bytes": _tree_rss(state.pid),
            "ctx_per_request": preset.ctx_per_request if preset else None,
            "parallel": preset.parallel if preset else None,
            "queue_depth": ctx.proxy.queue_depth(model_id),
            "gpu_layers": list(state.gpu_layers) if state.gpu_layers else None,
        })
    return {"data": out, "pid": os.getpid()}
