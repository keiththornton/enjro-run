"""strixrun command line."""

import logging
import sys
from typing import Annotated

import typer
import uvicorn

from strixrun import __version__
from strixrun.settings import SettingsError, SettingsStore, is_loopback_host

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


def main() -> None:
    app()


if __name__ == "__main__":
    sys.exit(main())
