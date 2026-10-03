"""Request history and its live feed."""

from fastapi import APIRouter, Request

from strixrun import errors
from strixrun.api.deps import Ctx
from strixrun.api.sse import sse_response
from strixrun.requestlog import record_row

router = APIRouter()


@router.get("/requests")
def list_requests(ctx: Ctx, limit: int = 100, before: float | None = None,
                  model_id: str | None = None, status: str | None = None):
    return {"data": ctx.requestlog.list(limit=limit, before=before, model_id=model_id, status=status)}


@router.get("/requests/events")
def request_events(request: Request, ctx: Ctx):
    return sse_response(request, ctx.bus, {"request.completed"}, transform=record_row)


@router.get("/requests/{request_id}")
def get_request(request_id: str, ctx: Ctx):
    row = ctx.requestlog.get(request_id)
    if row is None:
        raise errors.NotFound(f"request {request_id}")
    return row
