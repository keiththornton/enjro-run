"""Server control for the local machine (used by `strixrun stop` and the Windows shortcuts)."""

import ipaddress

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from strixrun import errors

router = APIRouter()


@router.post("/shutdown", status_code=202)
def shutdown(request: Request):
    # Only this computer may stop the server, even with a valid key.
    peer = request.client.host if request.client else ""
    try:
        local = ipaddress.ip_address(peer).is_loopback
    except ValueError:
        local = False
    if not local:
        raise errors.Forbidden("StrixRun can only be stopped from the computer it runs on")
    stop = getattr(request.app.state, "request_shutdown", None)
    if stop is None:
        return JSONResponse({"stopping": False, "reason": "not started by strixrun serve"}, status_code=501)
    stop()
    return {"stopping": True}
