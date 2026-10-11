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

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from enjrorun import errors, paths
from enjrorun.api import admin as admin_routes
from enjrorun.api import openai as openai_routes
from enjrorun.api import upstream as upstream_routes
from enjrorun.backend import procutil
from enjrorun.backend.supervisor import LocalProcessBackend
from enjrorun.binaries.manager import BinaryError, BinaryManager
from enjrorun.context import AppContext
from enjrorun.discover import Discover
from enjrorun.downloads import Downloads, HubClient
from enjrorun.events import EventBus
from enjrorun.instance import InstanceLock
from enjrorun.memory import MemoryEstimator
from enjrorun.metrics import Metrics
from enjrorun.presets import PresetError, PresetStore
from enjrorun.proxy import Proxy
from enjrorun.registry import Registry, RegistryError
from enjrorun.requestlog import RequestLog
from enjrorun.security import SecurityMiddleware
from enjrorun.settings import Settings, SettingsError, SettingsStore

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
                  load_timeout: float, hub=None) -> AppContext:
    bus = EventBus()

    def current() -> Settings:
        return store.current  # always the latest settings

    store.on_change(lambda s: bus.publish("settings.changed", s))
    registry = Registry(paths.registry_path())
    binaries = BinaryManager(bus=bus, settings=store)
    backend = LocalProcessBackend(
        bus=bus,
        binary=binary or binaries.command,
        templates_dir=paths.templates_dir(),
        pid_dir=paths.pid_dir(),
        state_path=paths.state_path(),
        load_timeout=load_timeout,
    )
    proxy = Proxy(registry, backend, bus, idle_timeout=lambda: current().upstream_idle_timeout_s)
    estimator = MemoryEstimator()
    hub = hub or HubClient()
    return AppContext(
        bus=bus,
        settings=store,
        registry=registry,
        presets=PresetStore(paths.presets_dir()),
        backend=backend,
        proxy=proxy,
        binaries=binaries,
        metrics=Metrics(bus, backend, proxy),
        estimator=estimator,
        downloads=Downloads(bus, registry, lambda: Path(current().models_dirs[0]), estimator=estimator, hub=hub),
        discover=Discover(hub, estimator),
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
    ctx.registry.refresh_stale_meta()


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
        except errors.EnjroError as e:
            log.warning("could not restore %s: %s", model_id, e.message)
    return restored


async def _install_on_start(ctx: AppContext) -> None:
    """First run: fetch the pinned llama-server in the background. Failure isn't fatal."""
    last_pct = -10

    def report(ev):
        nonlocal last_pct
        d = ev.data
        if d["phase"] == "download" and d.get("total"):
            pct = int(100 * d["done"] / d["total"])
            if pct >= last_pct + 10:
                last_pct = pct
                log.info("downloading %s: %d%%", d["asset"], pct)
        elif d["phase"] in ("verify", "extract", "smoke_test", "done"):
            log.info("llama-server install: %s", d["phase"])

    ctx.bus.on("binary.progress", report)
    try:
        log.info("llama-server not installed; installing llama.cpp %s", ctx.binaries.manifest.default)
        await ctx.binaries.install()
    except BinaryError as e:
        log.error("llama-server install failed: %s (retry from Settings in the dashboard)", e)


async def _sweep_forever(ctx: AppContext) -> None:
    while True:
        with contextlib.suppress(Exception):
            ctx.requestlog.sweep()
        await asyncio.sleep(SWEEP_INTERVAL_S)


def create_app(
    store: SettingsStore | None = None,
    binary: Callable[[], list[str] | None] | None = None,
    load_timeout: float = 300.0,
    hub=None,
) -> FastAPI:
    store = store or SettingsStore()
    if store.current is None:
        store.load()
    store.current.validate()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Before anything with side effects: a second server on the same data folder
        # would reap the first one's children and overwrite its restore list.
        lock = InstanceLock(paths.run_dir() / "server.lock")
        lock.acquire()
        try:
            async with _running(app):
                yield
        finally:
            lock.release()

    @asynccontextmanager
    async def _running(app: FastAPI):
        killed = procutil.reap_orphans(paths.pid_dir())
        if killed:
            log.warning("stopped %d llama-server process(es) left over from a previous run", len(killed))
        ctx = build_context(store, binary, load_timeout, hub)
        app.state.ctx = ctx
        installer = None
        if binary is None and ctx.binaries.command() is None and not store.current.binary_path:
            installer = asyncio.create_task(_install_on_start(ctx))
        rescan(ctx)
        if store.current.restore_loaded:
            await restore_loaded(ctx)
        sweeper = asyncio.create_task(_sweep_forever(ctx))
        try:
            yield
        finally:
            sweeper.cancel()
            if installer:
                installer.cancel()
            await ctx.downloads.aclose()
            await ctx.backend.shutdown()
            await ctx.proxy.aclose()
            ctx.requestlog.close()

    app = FastAPI(title="Enjro Run", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    errors.install_handlers(app)
    for exc_type in (PresetError, RegistryError, SettingsError):
        app.add_exception_handler(
            exc_type, lambda _r, e: errors.error_response(errors.BadRequest(str(e)))
        )
    app.include_router(openai_routes.router)
    app.include_router(admin_routes.router)
    app.include_router(upstream_routes.router)

    @app.get("/metrics", include_in_schema=False)
    def metrics(request: Request):
        return PlainTextResponse(request.app.state.ctx.metrics.render(),
                                 media_type="text/plain; version=0.0.4")

    _mount_dashboard(app)  # last: its catch-all route must not shadow API routes
    app.state.session_secret = _session_secret()
    app.add_middleware(SecurityMiddleware, settings=lambda: store.current,
                       session_secret=app.state.session_secret, port=lambda: store.current.port)
    app.add_middleware(SecurityHeaders)
    return app


def _mount_dashboard(app: FastAPI) -> None:
    index = WEB_DIST / "index.html"
    if not index.exists():
        @app.get("/", include_in_schema=False)
        def no_dashboard():
            return {"enjrorun": "running", "dashboard": "missing from this install; reinstall with "
                    "installer/install.sh (Linux, macOS) or the Windows installer"}
        return

    if (WEB_DIST / "assets").is_dir():
        app.mount("/assets", _ImmutableStatic(directory=WEB_DIST / "assets"), name="assets")

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def spa(path: str):
        if path.split("/", 1)[0] in {"v1", "admin", "upstream", "metrics"}:
            raise errors.NotFound(f"/{path}")
        candidate = (WEB_DIST / path).resolve()
        if path and WEB_DIST.resolve() in candidate.parents and candidate.is_file():
            return FileResponse(candidate, headers={"Cache-Control": "no-cache"})
        # Always revalidate the shell: it names the current build's hashed bundles.
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


class _ImmutableStatic(StaticFiles):
    """Vite's /assets files are content-hashed, so they never change under a name."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response
