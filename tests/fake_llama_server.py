"""A fake llama-server for tests.

Accepts (and mostly ignores) real llama-server flags, so the Supervisor can launch it
exactly like the real binary: [sys.executable, fake_llama_server.py, -m ..., --port N, ...].

Behaviour knobs (environment variables):
  FAKE_LOAD_DELAY     seconds /health returns 503 "Loading model" (default 0)
  FAKE_TOKEN_DELAY    seconds between streamed tokens (default 0)
  FAKE_TOKENS         number of content tokens to generate (default 5)
  FAKE_HANG_AFTER     stop sending (but keep the connection open) after N tokens
  FAKE_CRASH_AFTER    os._exit(1) after N streamed tokens
  FAKE_STARTUP_ERROR  oom | unknown_arch | cudart | port: print that signature to stderr, exit 1
  FAKE_EVENTS_FILE    append one line per notable event ("cancelled", "request <model>")
"""

import argparse
import asyncio
import json
import os
import sys
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

STARTUP_ERRORS = {
    "oom": "ggml_backend_cuda_buffer_type_alloc_buffer: allocating 9000.00 MiB on device 0: "
    "cudaMalloc failed: out of memory\nllama_model_load: error loading model: unable to "
    "allocate CUDA0 buffer",
    "unknown_arch": "llama_model_load: error loading model: error loading model architecture: "
    "unknown model architecture: 'fakearch'",
    "cudart": "error while loading shared libraries: libcudart.so.12: cannot open shared "
    "object file: No such file or directory",
    "port": "start: couldn't bind HTTP server socket, hostname: 127.0.0.1, port: {port}",
}


def env_float(name: str, default: float = 0.0) -> float:
    return float(os.environ.get(name) or default)


def env_int(name: str) -> int | None:
    v = os.environ.get(name)
    return int(v) if v else None


def record(event: str) -> None:
    path = os.environ.get("FAKE_EVENTS_FILE")
    if path:
        with open(path, "a") as f:
            f.write(event + "\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--api-key")
    p.add_argument("-m", "--model", default="fake.gguf")
    p.add_argument("--embeddings", "--embedding", action="store_true")
    p.add_argument("--version", action="store_true")
    args, _unknown = p.parse_known_args(argv)
    return args


def build_app(args: argparse.Namespace) -> FastAPI:
    app = FastAPI()
    started = time.monotonic()
    load_delay = env_float("FAKE_LOAD_DELAY")
    model_name = os.path.basename(args.model)

    def authorized(request: Request) -> bool:
        if not args.api_key:
            return True
        return request.headers.get("authorization") == f"Bearer {args.api_key}"

    def unauthorized() -> JSONResponse:
        return JSONResponse(
            {"error": {"code": 401, "message": "Invalid API Key", "type": "authentication_error"}},
            status_code=401,
        )

    @app.get("/health")
    def health():
        if time.monotonic() - started < load_delay:
            return JSONResponse(
                {"error": {"code": 503, "message": "Loading model", "type": "unavailable_error"}},
                status_code=503,
            )
        return {"status": "ok"}

    @app.get("/props")
    def props(request: Request):
        if not authorized(request):
            return unauthorized()
        return {"model_path": args.model, "total_slots": 2}

    @app.get("/slots")
    def slots(request: Request):
        if not authorized(request):
            return unauthorized()
        return [{"id": 0, "is_processing": False}, {"id": 1, "is_processing": False}]

    @app.post("/tokenize")
    async def tokenize(request: Request):
        if not authorized(request):
            return unauthorized()
        body = await request.json()
        return {"tokens": list(range(len(body.get("content", "").split())))}

    @app.post("/detokenize")
    async def detokenize(request: Request):
        if not authorized(request):
            return unauthorized()
        body = await request.json()
        return {"content": " ".join(f"t{t}" for t in body.get("tokens", []))}

    @app.post("/v1/embeddings")
    async def embeddings(request: Request):
        if not authorized(request):
            return unauthorized()
        body = await request.json()
        inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
        record(f"request {body.get('model')}")
        return {
            "object": "list",
            "model": body.get("model", model_name),
            "data": [
                {"object": "embedding", "index": i, "embedding": [0.1, 0.2, 0.3, 0.4]}
                for i, _ in enumerate(inputs)
            ],
            "usage": {"prompt_tokens": 3 * len(inputs), "total_tokens": 3 * len(inputs)},
        }

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        if not authorized(request):
            return unauthorized()
        body = await request.json()
        record(f"request {body.get('model')}")
        n_tokens = int(os.environ.get("FAKE_TOKENS", "5"))
        words = [f"tok{i} " for i in range(n_tokens)]
        use_tools = bool(body.get("tools"))
        model = body.get("model", model_name)
        cid = f"chatcmpl-fake{int(time.time() * 1000)}"
        timings = {
            "prompt_n": 7,
            "prompt_ms": 12.5,
            "predicted_n": n_tokens,
            "predicted_ms": 50.0,
            "predicted_per_second": n_tokens / 0.05,
        }
        usage = {"prompt_tokens": 7, "completion_tokens": n_tokens, "total_tokens": 7 + n_tokens}

        if not body.get("stream"):
            await asyncio.sleep(env_float("FAKE_TOKEN_DELAY") * n_tokens)
            if use_tools:
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [_tool_call()],
                }
                finish = "tool_calls"
            else:
                message = {"role": "assistant", "content": "".join(words)}
                finish = "stop"
            return {
                "id": cid,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": usage,
                "timings": timings,
            }

        include_usage = (body.get("stream_options") or {}).get("include_usage", False)
        return StreamingResponse(
            _stream(cid, model, words, use_tools, include_usage, usage, timings),
            media_type="text/event-stream",
        )

    return app


def _tool_call() -> dict:
    return {
        "id": "call_fake1",
        "type": "function",
        "function": {"name": "get_weather", "arguments": json.dumps({"city": "Paris"})},
    }


def _chunk(cid: str, model: str, delta: dict, finish: str | None) -> str:
    payload = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload)}\n\n"


async def _stream(cid, model, words, use_tools, include_usage, usage, timings):
    token_delay = env_float("FAKE_TOKEN_DELAY")
    hang_after = env_int("FAKE_HANG_AFTER")
    crash_after = env_int("FAKE_CRASH_AFTER")
    try:
        yield _chunk(cid, model, {"role": "assistant", "content": None}, None)
        if use_tools:
            call = _tool_call()
            yield _chunk(
                cid,
                model,
                {"tool_calls": [{"index": 0, "id": call["id"], "type": "function",
                                 "function": {"name": call["function"]["name"], "arguments": ""}}]},
                None,
            )
            yield _chunk(
                cid,
                model,
                {"tool_calls": [{"index": 0, "function": {"arguments": call["function"]["arguments"]}}]},
                None,
            )
            finish = "tool_calls"
        else:
            for i, w in enumerate(words):
                if crash_after is not None and i >= crash_after:
                    # Let already-sent tokens reach the client first: on Windows an abrupt
                    # exit resets the socket, and a reset can discard unread received data.
                    await asyncio.sleep(0.5)
                    sys.stderr.write("fake: simulated crash mid-stream\n")
                    sys.stderr.flush()
                    os._exit(1)
                if hang_after is not None and i >= hang_after:
                    await asyncio.sleep(3600)
                if token_delay:
                    await asyncio.sleep(token_delay)
                yield _chunk(cid, model, {"content": w}, None)
            finish = "stop"
        final = json.loads(_chunk(cid, model, {}, finish)[6:])
        final["timings"] = timings
        yield f"data: {json.dumps(final)}\n\n"
        if include_usage:
            yield f"data: {json.dumps({'id': cid, 'object': 'chat.completion.chunk', 'model': model, 'choices': [], 'usage': usage, 'timings': timings})}\n\n"
        yield "data: [DONE]\n\n"
    except (asyncio.CancelledError, GeneratorExit):
        record("cancelled")
        raise


def main(argv: list[str] | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.version:
        print("version: 0 (fake)")
        return
    sys.stderr.write(f"main: loading model '{args.model}'\n")
    sys.stderr.flush()
    err = os.environ.get("FAKE_STARTUP_ERROR")
    if err:
        sys.stderr.write(STARTUP_ERRORS[err].format(port=args.port) + "\n")
        sys.stderr.flush()
        sys.exit(1)
    sys.stderr.write(f"main: server is listening on http://{args.host}:{args.port}\n")
    sys.stderr.flush()
    uvicorn.run(build_app(args), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
