"""App factory and lifespan.

Startup: validate config → reap orphaned children → rescan models → restore models
that were loaded at the last shutdown. Shutdown: save loaded set, stop children.
"""

import asyncio
import contextlib
import logging
import os
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from strixrun import errors, paths
from strixrun.api import openai as openai_routes
from strixrun.backend import procutil
from strixrun.backend.supervisor import LocalProcessBackend
from strixrun.context import AppContext
from strixrun.events import EventBus
from strixrun.presets import PresetStore
from strixrun.proxy import Proxy
from strixrun.registry import Registry
from strixrun.requestlog import RequestLog
from strixrun.security import SecurityMiddleware
from strixrun.settings import Settings, SettingsStore

log = logging.getLogger(__name__)

WEB_DIST = Path(__file__).parent / "web_dist"
SWEEP_INTERVAL_S = 3600
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'"
)
SECURITY_HEADERS = [
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
]


class SecurityHeaders:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), *SECURITY_HEADERS]}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def default_binary(settings: Callable[[], Settings]) -> Callable[[], list[str] | None]:
    """Resolve the llama-server command: config override, else the installed pinned tag."""

    def resolve() -> list[str] | None:
        s = settings()
        if s.binary_path:
            return [s.binary_path] if Path(s.binary_path).exists() else None
        if s.binary_tag:
            exe = "llama-server.exe" if os.name == "nt" else "llama-server"
            for candidate in (paths.bin_dir() / s.binary_tag).rglob(exe):
                return [str(candidate)]
        return None

    return resolve


def _session_secret() -> bytes:
    path = paths.run_dir() / "session_secret"
    if path.exists():
        return bytes.fromhex(path.read_text().strip())
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(secret.hex())
    return secret


def build_context(store: SettingsStore, binary: Callable[[], list[str] | None] | None,
                  load_timeout: float) -> AppContext:
    bus = EventBus()

    def current() -> Settings:
        return store.current  # always the latest settings

    store.on_change(lambda s: bus.publish("settings.changed", s))
    registry = Registry(paths.registry_path())
    backend = LocalProcessBackend(
        bus=bus,
        binary=binary or default_binary(current),
        templates_dir=paths.templates_dir(),
        pid_dir=paths.pid_dir(),
        state_path=paths.state_path(),
        load_timeout=load_timeout,
    )
    return AppContext(
        bus=bus,
        settings=store,
        registry=registry,
        presets=PresetStore(paths.presets_dir()),
        backend=backend,
        proxy=Proxy(registry, backend, bus, idle_timeout=lambda: current().upstream_idle_timeout_s),
        requestlog=RequestLog(
            paths.db_path(), bus,
            ring_size=lambda: current().log_body_ring_size,
            retention_days=lambda: current().log_retention_days,
        ),
    )


def rescan(ctx: AppContext) -> None:
    s = ctx.settings.current
    ctx.registry.scan([Path(d) for d in s.models_dirs], location="store")
    ctx.registry.scan([Path(d) for d in s.extra_scan_dirs], location="scan")


async def restore_loaded(ctx: AppContext) -> list[str]:
    restored = []
    for model_id in LocalProcessBackend.read_state(paths.state_path()):
        entry = ctx.registry.get(model_id)
        if entry is None:
            log.warning("not restoring %s: no longer in the registry", model_id)
            continue
        try:
            await ctx.backend.load(entry, ctx.presets.load(entry.id, entry.meta))
            restored.append(model_id)
            log.info("restoring %s", model_id)
        except errors.StrixError as e:
            log.warning("could not restore %s: %s", model_id, e.message)
    return restored


async def _sweep_forever(ctx: AppContext) -> None:
    while True:
        with contextlib.suppress(Exception):
            ctx.requestlog.sweep()
        await asyncio.sleep(SWEEP_INTERVAL_S)


def create_app(
    store: SettingsStore | None = None,
    binary: Callable[[], list[str] | None] | None = None,
    load_timeout: float = 300.0,
) -> FastAPI:
    store = store or SettingsStore()
    if store.current is None:
        store.load()
    store.current.validate()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        killed = procutil.reap_orphans(paths.pid_dir())
        if killed:
            log.warning("stopped %d llama-server process(es) left over from a previous run", len(killed))
        ctx = build_context(store, binary, load_timeout)
        app.state.ctx = ctx
        rescan(ctx)
        if store.current.restore_loaded:
            await restore_loaded(ctx)
        sweeper = asyncio.create_task(_sweep_forever(ctx))
        try:
            yield
        finally:
            sweeper.cancel()
            await ctx.backend.shutdown()
            await ctx.proxy.aclose()
            ctx.requestlog.close()

    app = FastAPI(title="StrixRun", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    errors.install_handlers(app)
    app.include_router(openai_routes.router)
    _mount_dashboard(app)
    app.add_middleware(SecurityMiddleware, settings=lambda: store.current,
                       session_secret=_session_secret(), port=lambda: store.current.port)
    app.add_middleware(SecurityHeaders)
    return app


def _mount_dashboard(app: FastAPI) -> None:
    index = WEB_DIST / "index.html"
    if not index.exists():
        @app.get("/", include_in_schema=False)
        def no_dashboard():
            return {"strixrun": "running", "dashboard": "not built (run `npm run build` in web/)"}
        return

    if (WEB_DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.split("/", 1)[0] in {"v1", "admin", "upstream", "metrics"}:
            raise errors.NotFound(f"/{path}")
        candidate = (WEB_DIST / path).resolve()
        if path and WEB_DIST.resolve() in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)
