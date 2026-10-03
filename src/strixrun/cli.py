"""strixrun command line."""

import logging
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


@app.command()
def serve(
    port: Annotated[int | None, typer.Option(help="Port to listen on (default: from config, 8790).")] = None,
    log_level: Annotated[str, typer.Option(help="debug, info, warning or error.")] = "info",
) -> None:
    """Run the StrixRun server."""
    from strixrun.app import create_app

    logging.basicConfig(level=log_level.upper(), format="%(levelname)s %(name)s: %(message)s")
    store = SettingsStore()
    try:
        settings = store.load()
        if port is not None:
            settings.port = port
        application = create_app(store)
    except SettingsError as e:
        _fail(str(e))
        return
    shown = "127.0.0.1" if is_loopback_host(settings.host) or settings.host in ("0.0.0.0", "::") else settings.host
    typer.echo(f"StrixRun {__version__} on http://{shown}:{settings.port}  (OpenAI API at /v1)")
    if not is_loopback_host(settings.host):
        typer.echo(f"Listening on {settings.host}: other devices need the API key.")
    uvicorn.run(
        application,
        host=settings.host,
        port=settings.port,
        proxy_headers=False,  # auth trusts the socket peer, never X-Forwarded-For
        log_level=log_level,
        timeout_graceful_shutdown=20,
    )


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


def main() -> None:
    app()


if __name__ == "__main__":
    sys.exit(main())
