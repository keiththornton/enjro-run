"""Shared route helpers."""

from typing import Annotated

from fastapi import Depends, Request

from strixrun.context import AppContext
from strixrun.proxy import RequestMeta


def get_context(request: Request) -> AppContext:
    return request.app.state.ctx


def request_meta(request: Request) -> RequestMeta:
    return RequestMeta(
        client_ip=request.client.host if request.client else None,
        key_id=request.scope.get("state", {}).get("key_id"),
    )


Ctx = Annotated[AppContext, Depends(get_context)]
