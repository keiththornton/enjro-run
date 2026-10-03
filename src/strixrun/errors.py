"""OpenAI-shaped errors: {"error": {"message", "type", "code"}}."""

from types import MappingProxyType
from typing import ClassVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class StrixError(Exception):
    status = 500
    type = "server_error"
    code = "internal_error"
    headers: ClassVar = MappingProxyType({})

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def extra(self) -> dict:
        return {}

    def body(self) -> dict:
        return {"error": {"message": self.message, "type": self.type, "code": self.code}, **self.extra()}


class BadRequest(StrixError):
    status, type, code = 400, "invalid_request_error", "bad_request"


class Unauthorized(StrixError):
    status, type, code = 401, "authentication_error", "invalid_api_key"


class Forbidden(StrixError):
    status, type, code = 403, "permission_error", "forbidden"


class Conflict(StrixError):
    status, type, code = 409, "invalid_request_error", "conflict"


class NotFound(StrixError):
    status, type, code = 404, "invalid_request_error", "not_found"

    def __init__(self, path: str) -> None:
        super().__init__(f"{path} does not exist")


class ModelNotFound(StrixError):
    status, type, code = 404, "invalid_request_error", "model_not_found"

    def __init__(self, model: str) -> None:
        super().__init__(
            f"model {model!r} does not exist. GET /v1/models?all=1 lists downloaded models."
        )


class ModelNotLoaded(StrixError):
    status, type, code = 409, "invalid_request_error", "model_not_loaded"

    def __init__(self, model: str) -> None:
        super().__init__(
            f"model {model!r} is not loaded. Load it in the StrixRun dashboard "
            f"or run `strixrun load {model}`."
        )


class ModelCrashed(StrixError):
    status, type, code = 409, "server_error", "model_crashed"

    def __init__(self, model: str, reason: str) -> None:
        super().__init__(
            f"model {model!r} crashed ({reason}). See its logs in the dashboard, "
            f"then reload it with `strixrun load {model}`."
        )


class AlreadyLoaded(StrixError):
    status, type, code = 409, "invalid_request_error", "model_already_loaded"

    def __init__(self, model: str) -> None:
        super().__init__(f"model {model!r} is already loaded; unload it first to change settings.")


class NeedsConfirmation(StrixError):
    status, type, code = 409, "invalid_request_error", "needs_confirmation"

    def __init__(self, estimate: dict) -> None:
        super().__init__(
            f"estimated memory is {estimate.get('verdict')} for this machine; "
            "resend with confirm=true to load anyway."
        )
        self.estimate = estimate

    def extra(self) -> dict:
        return {"estimate": self.estimate}


class BinaryMissing(StrixError):
    status, type, code = 409, "server_error", "binary_missing"

    def __init__(self) -> None:
        super().__init__(
            "llama-server is not installed. Install it from Settings in the dashboard "
            "or restart `strixrun serve`."
        )


class QueueFull(StrixError):
    status, type, code = 429, "rate_limit_error", "queue_full"

    def __init__(self, model: str) -> None:
        super().__init__(f"model {model!r} is busy and its queue is full; retry shortly.")


class UpstreamError(StrixError):
    status, type, code = 502, "server_error", "upstream_error"

    def __init__(self, model: str, detail: str) -> None:
        super().__init__(f"model {model!r} failed: {detail}")


class ModelLoading(StrixError):
    status, type, code = 503, "server_error", "model_loading"
    headers: ClassVar = MappingProxyType({"Retry-After": "5"})

    def __init__(self, model: str) -> None:
        super().__init__(f"model {model!r} is still loading; retry shortly.")


def error_response(exc: StrixError) -> JSONResponse:
    return JSONResponse(exc.body(), status_code=exc.status, headers=dict(exc.headers))


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(StrixError)
    async def _strix(_: Request, exc: StrixError) -> JSONResponse:
        return error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", ()))
        return error_response(BadRequest(f"{loc}: {first.get('msg', 'invalid request')}"))
