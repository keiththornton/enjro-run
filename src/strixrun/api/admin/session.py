"""Browser sessions: exchange the API key for an HttpOnly cookie."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from strixrun import errors
from strixrun.api.deps import Ctx
from strixrun.security import COOKIE_NAME, session_cookie_value
from strixrun.settings import verify_api_key

router = APIRouter()


class SessionIn(BaseModel):
    key: str


@router.post("/session")
def create_session(body: SessionIn, request: Request, ctx: Ctx):
    s = ctx.settings.current
    if not s.api_key_hash:
        return {"authenticated": True, "key_required": False}
    if not verify_api_key(body.key, s.api_key_hash):
        raise errors.Unauthorized("that API key is not valid")
    resp = JSONResponse({"authenticated": True, "key_required": True})
    resp.set_cookie(
        COOKIE_NAME, session_cookie_value(request.app.state.session_secret, s.api_key_hash),
        httponly=True, samesite="strict", path="/", max_age=30 * 86400,
    )
    return resp


@router.get("/session")
def get_session(request: Request, ctx: Ctx):
    return {
        "authenticated": True,
        "via": request.scope.get("state", {}).get("key_id"),
        "key_required": ctx.settings.current.api_key_hash is not None,
    }


@router.delete("/session")
def delete_session():
    resp = JSONResponse({"authenticated": False})
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp
