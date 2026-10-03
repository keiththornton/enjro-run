import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from strixrun import errors as E


def make_app(exc: Exception) -> TestClient:
    app = FastAPI()
    E.install_handlers(app)

    @app.get("/boom")
    def boom():
        raise exc

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "exc,status,etype,code",
    [
        (E.ModelNotFound("x"), 404, "invalid_request_error", "model_not_found"),
        (E.ModelNotLoaded("x"), 409, "invalid_request_error", "model_not_loaded"),
        (E.ModelCrashed("x", "oom"), 409, "server_error", "model_crashed"),
        (E.BinaryMissing(), 409, "server_error", "binary_missing"),
        (E.QueueFull("x"), 429, "rate_limit_error", "queue_full"),
        (E.UpstreamError("x", "boom"), 502, "server_error", "upstream_error"),
        (E.ModelLoading("x"), 503, "server_error", "model_loading"),
        (E.BadRequest("nope"), 400, "invalid_request_error", "bad_request"),
    ],
)
def test_error_shapes(exc, status, etype, code):
    r = make_app(exc).get("/boom")
    assert r.status_code == status
    body = r.json()
    assert set(body) == {"error"}
    assert body["error"]["type"] == etype
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]


def test_not_loaded_message_names_both_actions():
    r = make_app(E.ModelNotLoaded("qwen:q4")).get("/boom")
    msg = r.json()["error"]["message"]
    assert "dashboard" in msg and "strixrun load qwen:q4" in msg


def test_crashed_message_includes_reason():
    r = make_app(E.ModelCrashed("m", "out of memory")).get("/boom")
    assert "out of memory" in r.json()["error"]["message"]


def test_loading_sets_retry_after():
    r = make_app(E.ModelLoading("m")).get("/boom")
    assert r.headers["retry-after"] == "5"


def test_needs_confirmation_carries_estimate():
    est = {"estimate_bytes": 10, "available_bytes": 11, "verdict": "tight"}
    r = make_app(E.NeedsConfirmation(est)).get("/boom")
    assert r.status_code == 409
    body = r.json()
    assert body["error"]["code"] == "needs_confirmation"
    assert body["estimate"] == est


def test_request_validation_errors_use_openai_shape():
    app = FastAPI()
    E.install_handlers(app)

    @app.post("/v")
    def v(n: int):
        return n

    r = TestClient(app).post("/v?n=notint")
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "invalid_request_error"
