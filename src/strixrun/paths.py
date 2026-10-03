"""Filesystem locations. Everything lives under STRIX_HOME (default ~/.strix)/run."""

import os
from pathlib import Path


def strix_home() -> Path:
    env = os.environ.get("STRIX_HOME")
    return Path(env).expanduser() if env else Path.home() / ".strix"


def run_dir() -> Path:
    return strix_home() / "run"


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
    return run_dir() / "strixrun.db"


def pid_dir() -> Path:
    return run_dir() / "run"


def state_path() -> Path:
    return run_dir() / "state.json"
