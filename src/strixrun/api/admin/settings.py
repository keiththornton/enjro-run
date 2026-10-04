"""Settings: read, update (non-file-only fields), API key, Hugging Face token."""

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from strixrun import errors, hftoken
from strixrun.api.deps import Ctx
from strixrun.security import COOKIE_NAME, set_session_cookie
from strixrun.settings import generate_api_key, hash_api_key

router = APIRouter()


def settings_view(ctx) -> dict:
    return {**ctx.settings.current.public_dict(), "hf_token": hftoken.token_status()}


@router.get("/settings")
def get_settings(ctx: Ctx):
    return settings_view(ctx)


@router.put("/settings")
def update_settings(body: dict, ctx: Ctx):
    ctx.settings.update_from_api(body)  # SettingsError → 400 (file-only, unknown, invalid)
    return settings_view(ctx)


class ApiKeyIn(BaseModel):
    action: Literal["generate", "rotate", "clear"]


@router.post("/settings/api-key")
def api_key(body: ApiKeyIn, request: Request, ctx: Ctx):
    if body.action == "clear":
        ctx.settings.set_api_key_hash(None)
        resp = JSONResponse({"key": None, **settings_view(ctx)})
        resp.delete_cookie(COOKIE_NAME, path="/")
        return resp
    key = generate_api_key()
    s = ctx.settings.set_api_key_hash(hash_api_key(key))
    # Shown once. The caller's browser session is re-issued for the new key; every
    # other session (bound to the old key hash) stops working.
    resp = JSONResponse({"key": key, **settings_view(ctx)})
    set_session_cookie(resp, request, s)
    return resp


class HfTokenIn(BaseModel):
    token: str | None


@router.put("/settings/hf-token")
def hf_token(body: HfTokenIn, ctx: Ctx):
    try:
        status = hftoken.set_token(body.token)
    except ValueError as e:
        raise errors.BadRequest(str(e)) from e
    ctx.downloads._cache.clear()  # gated repos may be readable now
    return {"hf_token": status}
