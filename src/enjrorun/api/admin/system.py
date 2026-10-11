"""Server control for the local machine (used by `enjrorun stop` and the Windows shortcuts)."""

import ipaddress

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from enjrorun import errors

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
        raise errors.Forbidden("Enjro Run can only be stopped from the computer it runs on")
    stop = getattr(request.app.state, "request_shutdown", None)
    if stop is None:
        return JSONResponse({"stopping": False, "reason": "not started by enjrorun serve"}, status_code=501)
    stop()
    return {"stopping": True}


@router.get("/system")
def system_info(request: Request):
    """What Enjro Run detects about this machine's memory (shown on the Settings page)."""
    from enjrorun.presets import Preset

    ctx = request.app.state.ctx
    probe = ctx.estimator.probe
    gpus = probe.gpus() if hasattr(probe, "gpus") else []
    placed = ctx.estimator.place(0, Preset())
    return {
        "ram_total_bytes": probe.total_ram(),
        "ram_available_bytes": probe.available_ram(),
        "gpus": gpus,
        "apple_silicon": probe.is_apple_silicon(),
        "metal_limit_bytes": placed["available_bytes"] if placed["limit"] == "metal" else None,
        "default_limit": placed["limit"],
        "default_available_bytes": placed["available_bytes"],
    }
