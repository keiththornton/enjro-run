"""strixrun command line."""

import logging
import os
import sys
from typing import Annotated

import typer
import uvicorn

from strixrun import __version__
from strixrun.settings import (
    FILE_ONLY_FIELDS,
    Settings,
    SettingsError,
    SettingsStore,
    generate_api_key,
    hash_api_key,
    is_loopback_host,
)

app = typer.Typer(no_args_is_help=True, add_completion=False, help="StrixRun: local LLM server.")


def _fail(message: str) -> None:
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


@app.command()
def version() -> None:
    """Print the StrixRun version."""
    typer.echo(__version__)


def _port_free(host: str, port: int) -> bool:
    import socket

    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as s:
        if os.name != "nt":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # match uvicorn: ignore TIME_WAIT
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


@app.command()
def serve(
    port: Annotated[int | None, typer.Option(help="Port to listen on (default: from config, 8790).")] = None,
    log_level: Annotated[str, typer.Option(help="debug, info, warning or error.")] = "info",
    log_file: Annotated[str | None, typer.Option(help="Write logs to this file (used by the login service).")] = None,
) -> None:
    """Run the StrixRun server."""
    from strixrun.app import create_app

    if log_file:
        from pathlib import Path

        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        stream = open(log_file, "a", buffering=1, encoding="utf-8")  # noqa: SIM115 - lives for the process
        sys.stdout = sys.stderr = stream  # no console under launchd/pythonw
    logging.basicConfig(level=log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stderr, force=True)
    debug = log_level.lower() == "debug"
    if not debug:
        # Health polls and proxied calls would log a line every 250 ms; the Requests page
        # is the request log.
        logging.getLogger("httpx").setLevel(logging.WARNING)
    store = SettingsStore()
    try:
        settings = store.load()
        if port is not None:
            settings.port = port
        application = create_app(store)
    except SettingsError as e:
        _fail(str(e))
        return
    from strixrun import paths
    from strixrun.instance import AlreadyRunning, InstanceLock

    probe = InstanceLock(paths.run_dir() / "server.lock")
    try:
        probe.acquire()  # the lifespan takes it for real; this gives a clean message instead of a traceback
    except AlreadyRunning as e:
        _fail(str(e))
    probe.release()
    if not _port_free(settings.host, settings.port):
        _fail(f"port {settings.port} is already in use (another StrixRun, or another program). "
              f"Stop it, or use --port.")
    shown = "127.0.0.1" if is_loopback_host(settings.host) or settings.host in ("0.0.0.0", "::") else settings.host
    typer.echo(f"StrixRun {__version__} on http://{shown}:{settings.port}  (OpenAI API at /v1)")
    if not is_loopback_host(settings.host):
        typer.echo(f"Listening on {settings.host}: other devices need the API key.")
    server = uvicorn.Server(uvicorn.Config(
        application,
        host=settings.host,
        port=settings.port,
        proxy_headers=False,  # auth trusts the socket peer, never X-Forwarded-For
        log_level=log_level,
        access_log=debug,  # dashboard polling would flood the terminal; --log-level debug shows it
        timeout_graceful_shutdown=20,
    ))
    application.state.request_shutdown = lambda: setattr(server, "should_exit", True)
    server.run()


key_app = typer.Typer(no_args_is_help=True, help="Manage the API key (works while the server is stopped).")
config_app = typer.Typer(no_args_is_help=True, help="Read and change config.json.")
app.add_typer(key_app, name="key")
app.add_typer(config_app, name="config")


def _save(store: SettingsStore, s: Settings) -> None:
    try:
        s.validate()
    except SettingsError as e:
        _fail(str(e))
    store.save(s)


@key_app.command("set")
def key_set() -> None:
    """Create a new API key (replaces any existing key) and print it once."""
    store = SettingsStore()
    s = store.load()
    key = generate_api_key()
    s.api_key_hash = hash_api_key(key)
    _save(store, s)
    typer.echo(key)
    typer.secho("Copy this key now: it isn't stored and won't be shown again. "
                "A running server picks it up after a restart.", err=True, fg=typer.colors.YELLOW)


@key_app.command("rotate")
def key_rotate() -> None:
    """Replace the API key; existing clients and browser sessions stop working."""
    key_set()


@key_app.command("clear")
def key_clear() -> None:
    """Remove the API key (only allowed while StrixRun listens on this computer only)."""
    store = SettingsStore()
    s = store.load()
    s.api_key_hash = None
    _save(store, s)
    typer.echo("API key removed.")


def _parse(field: str, raw: str):
    if field in ("models_dirs", "extra_scan_dirs"):
        from pathlib import Path

        return [str(Path(p.strip()).expanduser().resolve()) for p in raw.split(",") if p.strip()]
    default = getattr(Settings(), field)
    if isinstance(default, bool):
        if raw.lower() not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
            _fail(f"{field} must be true or false")
        return raw.lower() in ("true", "1", "yes", "on")
    if isinstance(default, int):
        try:
            return int(raw)
        except ValueError:
            _fail(f"{field} must be a whole number")
    if field == "binary_path":
        from pathlib import Path

        if raw in ("", "none"):
            return None
        path = Path(raw).expanduser().resolve()
        if not path.is_file():
            _fail(f"{path} is not a file")
        return str(path)
    return raw if raw not in ("none", "") else None


@config_app.command("set")
def config_set(field: str, value: str) -> None:
    """Set a config field, including file-only ones (host, binary_path, models_dirs, extra_scan_dirs)."""
    known = {f for f in Settings.__dataclass_fields__ if f != "api_key_hash"}
    if field not in known:
        _fail(f"unknown field {field!r}; known: {', '.join(sorted(known))}")
    store = SettingsStore()
    s = store.load()
    setattr(s, field, _parse(field, value))
    _save(store, s)
    typer.echo(f"{field} = {getattr(s, field)}")
    if field in FILE_ONLY_FIELDS or field == "port":
        typer.echo("Restart StrixRun for this to take effect.")


@config_app.command("show")
def config_show() -> None:
    """Print the current config (without the key hash)."""
    import json

    typer.echo(json.dumps(SettingsStore().load().public_dict(), indent=2))


# ---- commands that talk to a running server -----------------------------------------------


class _Client:
    def __init__(self, url: str | None, key: str | None) -> None:
        import os

        import httpx

        if url is None:
            port = SettingsStore().load().port
            url = os.environ.get("STRIXRUN_URL", f"http://127.0.0.1:{port}")
        key = key or os.environ.get("STRIXRUN_API_KEY")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        self.url = url.rstrip("/")
        self.http = httpx.Client(base_url=self.url, headers=headers, timeout=30)

    def call(self, method: str, path: str, body: dict | None = None):
        import httpx

        try:
            r = self.http.request(method, path, json=body if method != "GET" else None)
        except httpx.ConnectError:
            _fail(f"StrixRun isn't running at {self.url}. Start it with `strixrun serve`.")
        if r.status_code == 401:
            _fail("this server needs the API key: pass --key or set STRIXRUN_API_KEY")
        return r

    def resolve(self, name: str) -> dict:
        models = self.call("GET", "/admin/models").json()["data"]
        for m in models:
            if name == m["id"] or name in m["aliases"]:
                return m
        _fail(f"no model named {name!r}. Downloaded: {', '.join(m['aliases'][0] for m in models) or 'none'}")
        raise AssertionError  # unreachable


def _error(r) -> str:
    try:
        return r.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return f"HTTP {r.status_code}"


URL_OPT = typer.Option("--url", help="Server URL (default: http://127.0.0.1:<config port>, or STRIXRUN_URL).")
KEY_OPT = typer.Option("--key", help="API key, if the server requires one (or STRIXRUN_API_KEY).")


@app.command()
def load(
    model: Annotated[str, typer.Argument(help="Model alias or id.")],
    ctx: Annotated[int | None, typer.Option(help="Context per request, in tokens.")] = None,
    parallel: Annotated[int | None, typer.Option(help="Requests served at once.")] = None,
    gpu_layers: Annotated[int | None, typer.Option(help="Layers to offload (999 = all).")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Load even if memory looks tight.")] = False,
    no_wait: Annotated[bool, typer.Option("--no-wait", help="Return once loading starts.")] = False,
    url: Annotated[str | None, URL_OPT] = None,
    key: Annotated[str | None, KEY_OPT] = None,
) -> None:
    """Load a model and wait until it's serving."""
    import time

    c = _Client(url, key)
    m = c.resolve(model)
    preset = {k: v for k, v in {"ctx_per_request": ctx, "parallel": parallel, "n_gpu_layers": gpu_layers}.items()
              if v is not None}
    r = c.call("POST", f"/admin/models/{m['id']}/load", {"preset": preset, "confirm": yes})
    if r.status_code == 409 and r.json().get("error", {}).get("code") == "needs_confirmation":
        est = r.json()["estimate"]
        typer.echo(f"Memory looks {est['verdict']}: needs about {est['estimate_bytes'] / 1e9:.1f} GB "
                   f"of {est['available_bytes'] / 1e9:.1f} GB available.")
        if not typer.confirm("Load anyway?"):
            raise typer.Exit(1)
        r = c.call("POST", f"/admin/models/{m['id']}/load", {"preset": preset, "confirm": True})
    if r.status_code >= 400:
        _fail(_error(r))
    alias = m["aliases"][0]
    if no_wait:
        typer.echo(f"Loading {alias}")
        return
    start = time.monotonic()
    tty = sys.stdout.isatty()
    clear = "\r\033[K" if tty else ""
    while True:
        state = c.call("GET", f"/admin/models/{m['id']}").json()["state"]
        if state["status"] == "ready":
            typer.echo(f"{clear}{alias} is ready ({time.monotonic() - start:.1f}s). Use model \"{alias}\" at {c.url}/v1")
            return
        if state["status"] in ("crashed", "stopped"):
            typer.echo(clear, nl=False)
            logs = c.call("GET", f"/admin/models/{m['id']}/logs").json()["lines"][-15:]
            for line in logs:
                typer.secho(f"  {line}", err=True, fg=typer.colors.BRIGHT_BLACK)
            _fail(f"{alias} couldn't load ({state.get('crash_class') or state['status']}): {state.get('reason')}")
        if tty:
            typer.echo(f"{clear}Loading {alias}… {time.monotonic() - start:.0f}s", nl=False)
        time.sleep(0.5)


@app.command()
def unload(
    model: Annotated[str, typer.Argument(help="Model alias or id.")],
    url: Annotated[str | None, URL_OPT] = None,
    key: Annotated[str | None, KEY_OPT] = None,
) -> None:
    """Unload a model."""
    c = _Client(url, key)
    m = c.resolve(model)
    r = c.call("POST", f"/admin/models/{m['id']}/unload", {})
    if r.status_code >= 400:
        _fail(_error(r))
    typer.echo(f"Unloaded {m['aliases'][0]}")


@app.command()
def ps(
    url: Annotated[str | None, URL_OPT] = None,
    key: Annotated[str | None, KEY_OPT] = None,
) -> None:
    """Show loaded models."""
    rows = _Client(url, key).call("GET", "/admin/ps").json()["data"]
    if not rows:
        typer.echo("No models loaded.")
        return
    header = f"{'MODEL':40} {'STATUS':8} {'CTX':>7} {'SLOTS':>5} {'QUEUE':>5} {'MEMORY':>8} {'UP':>8}"
    typer.echo(header)
    for r in rows:
        mem = f"{r['rss_bytes'] / 1e9:.1f}G" if r.get("rss_bytes") else "-"
        up = f"{int(r['uptime_s'])}s" if r.get("uptime_s") is not None else "-"
        typer.echo(f"{r['aliases'][0][:40]:40} {r['status']:8} {r['ctx_per_request'] or '-':>7} "
                   f"{r['parallel'] or '-':>5} {r['queue_depth']:>5} {mem:>8} {up:>8}")


service_app = typer.Typer(no_args_is_help=True, help="Start StrixRun automatically when you log in.")
app.add_typer(service_app, name="service")


@service_app.command("install")
def service_install() -> None:
    """Run StrixRun at login (launchd / Task Scheduler / systemd --user)."""
    from strixrun import service

    try:
        typer.echo(service.install())
    except service.ServiceError as e:
        _fail(str(e))


@service_app.command("uninstall")
def service_uninstall() -> None:
    """Stop running StrixRun at login."""
    from strixrun import service

    typer.echo(service.uninstall())


def _local_url() -> str:
    return f"http://127.0.0.1:{SettingsStore().load().port}"


def _answering(url: str) -> bool:
    import httpx

    try:
        httpx.get(f"{url}/v1/models", timeout=1.0)
        return True  # any HTTP answer (even 401) means a server is there
    except httpx.TransportError:
        return False


def _notify(message: str) -> None:
    """Print, and on Windows without a console (Start Menu shortcut) show a message box."""
    typer.echo(message, err=True)
    if os.name == "nt" and (sys.stderr is None or not sys.stderr.isatty()):
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "StrixRun", 0x10)


@app.command("open")
def open_dashboard(
    no_browser: Annotated[bool, typer.Option("--no-browser", help="Don't open the dashboard.")] = False,
    timeout: Annotated[float, typer.Option(help="Seconds to wait for the server to start.")] = 60.0,
) -> None:
    """Start StrixRun in the background if it isn't running, then open the dashboard."""
    import subprocess
    import time
    import webbrowser
    from pathlib import Path

    from strixrun import paths

    url = _local_url()
    if _answering(url):
        typer.echo(f"StrixRun is already running at {url}")
    else:
        log = paths.run_dir() / "logs" / "serve.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        exe = Path(sys.executable)
        pythonw = exe.with_name("pythonw.exe")
        cmd = [str(pythonw if os.name == "nt" and pythonw.exists() else exe), "-m", "strixrun", "serve", "--log-file", str(log)]
        kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            kwargs["creationflags"] = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                                       | subprocess.CREATE_NO_WINDOW)
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(cmd, **kwargs)
        deadline = time.monotonic() + timeout
        while not _answering(url):
            if proc.poll() is not None:
                tail = log.read_text(errors="replace").strip().splitlines()[-1:] if log.exists() else []
                _notify(f"StrixRun didn't start: {tail[0] if tail else 'see ' + str(log)}")
                raise typer.Exit(1)
            if time.monotonic() > deadline:
                _notify(f"StrixRun didn't answer within {timeout:g}s; see {log}")
                raise typer.Exit(1)
            time.sleep(0.3)
        typer.echo(f"StrixRun is running at {url}")
    if not no_browser:
        webbrowser.open(url)


@app.command()
def stop(
    timeout: Annotated[float, typer.Option(help="Seconds to wait for a clean stop.")] = 30.0,
) -> None:
    """Stop the StrixRun server running on this computer."""
    import time

    import httpx

    url = _local_url()
    if not _answering(url):
        typer.echo("StrixRun isn't running.")
        return
    r = httpx.post(f"{url}/admin/shutdown", json={}, timeout=5)
    if r.status_code != 202:
        _fail(_error(r))
    deadline = time.monotonic() + timeout
    while _answering(url):
        if time.monotonic() > deadline:
            _fail(f"StrixRun is still running after {timeout:g}s")
        time.sleep(0.3)
    typer.echo("Stopped StrixRun.")


def main() -> None:
    app()


if __name__ == "__main__":
    sys.exit(main())
