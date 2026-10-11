"""Filesystem locations. Everything lives under ENJRO_HOME (default ~/.enjro)/run.

Enjro Run was called StrixRun and kept its data in ~/.strix/run. Until migrate.py has
moved that folder (it can't while an old server holds files open), the old folder
keeps being used, so nothing is ever lost or split between two places.
"""

import os
from pathlib import Path

LEGACY_DB_NAME = "strixrun.db"


def env(name: str) -> str | None:
    """ENJRORUN_<name>, falling back to the pre-rename STRIXRUN_<name>."""
    return os.environ.get(f"ENJRORUN_{name}") or os.environ.get(f"STRIXRUN_{name}")


def enjro_home() -> Path:
    custom = os.environ.get("ENJRO_HOME") or os.environ.get("STRIX_HOME")
    return Path(custom).expanduser() if custom else Path.home() / ".enjro"


def legacy_run_dir() -> Path | None:
    """The pre-rename data folder, when it applies (no custom home was set)."""
    if os.environ.get("ENJRO_HOME") or os.environ.get("STRIX_HOME"):
        return None
    return Path.home() / ".strix" / "run"


def run_dir() -> Path:
    new = enjro_home() / "run"
    legacy = legacy_run_dir()
    if legacy is not None and not new.exists() and legacy.is_dir():
        return legacy
    return new


def config_path() -> Path:
    return run_dir() / "config.json"


def registry_path() -> Path:
    return run_dir() / "models.json"


def presets_dir() -> Path:
    return run_dir() / "presets"


def templates_dir() -> Path:
    return run_dir() / "templates"


def default_models_dir() -> Path:
    return run_dir() / "models"


def bin_dir() -> Path:
    return run_dir() / "bin"


def db_path() -> Path:
    d = run_dir()
    # A folder still in its old place (or moved from it) keeps its old database name.
    legacy = d / LEGACY_DB_NAME
    return legacy if legacy.exists() else d / "enjrorun.db"


def pid_dir() -> Path:
    return run_dir() / "run"


def state_path() -> Path:
    return run_dir() / "state.json"
