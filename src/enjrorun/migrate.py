"""Move pre-rename (StrixRun) data and login services to their Enjro Run names.

Data: ~/.strix/run is renamed to ~/.enjro/run (one rename, so models are never copied),
then absolute paths inside its JSON files are rewritten. It is skipped while an old
server still holds the folder, and retried on the next start; until then paths.run_dir()
keeps using the old folder. A custom ENJRO_HOME/STRIX_HOME is used where it is.

Login service: a StrixRun launchd agent, systemd user unit or Task Scheduler task runs a
program that no longer exists after the upgrade, so it is removed and, if there was one,
an Enjro Run service is installed in its place.
"""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from enjrorun import paths, service
from enjrorun.instance import AlreadyRunning, InstanceLock
from enjrorun.winproc import hidden

log = logging.getLogger(__name__)

LEGACY_LABEL = "com.strixrun.serve"
LEGACY_TASK_NAME = "StrixRun"
LEGACY_UNIT_NAME = "strixrun.service"


class MigrationBlocked(RuntimeError):
    pass


# ---- data folder -------------------------------------------------------------------------


def _rewrite(value, old: str, new: str):
    if isinstance(value, str):
        for sep in (os.sep, "/"):
            if value == old or value.startswith(old + sep):
                return new + value[len(old):]
        return value
    if isinstance(value, list):
        return [_rewrite(v, old, new) for v in value]
    if isinstance(value, dict):
        return {k: _rewrite(v, old, new) for k, v in value.items()}
    return value


def _rewrite_json_paths(root: Path, old: Path) -> None:
    files = [*root.glob("*.json"), *root.glob("presets/*.json"), *root.glob("templates/*.json")]
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("migration: could not read %s; left as is", f)
            continue
        updated = _rewrite(data, str(old), str(root))
        if updated == data:
            continue
        mode = f.stat().st_mode & 0o777
        tmp = f.with_suffix(f.suffix + ".tmp")
        tmp.write_text(json.dumps(updated, indent=2), encoding="utf-8")
        os.chmod(tmp, mode)
        os.replace(tmp, f)


def _rename_db(root: Path) -> None:
    old = root / paths.LEGACY_DB_NAME
    if not old.exists() or (root / "enjrorun.db").exists():
        return
    for suffix in ("", "-wal", "-shm"):
        src = root / f"{paths.LEGACY_DB_NAME}{suffix}"
        if src.exists():
            os.replace(src, root / f"enjrorun.db{suffix}")


def migrate_data() -> str | None:
    """Move ~/.strix/run to ~/.enjro/run. Returns what happened, or None if nothing to do.

    Raises MigrationBlocked if the old folder is in use; nothing is changed then.
    """
    legacy = paths.legacy_run_dir()
    new = paths.enjro_home() / "run"
    if legacy is None or not legacy.is_dir() or new.exists():
        return None
    lock = InstanceLock(legacy / "server.lock")
    try:
        lock.acquire()
    except AlreadyRunning:
        raise MigrationBlocked(
            f"an older StrixRun is still running from {legacy}; stop it, then run `enjrorun migrate`"
        ) from None
    lock.release()  # Windows can't rename a folder with a file open in it
    new.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(legacy, new)
    except OSError as e:
        raise MigrationBlocked(
            f"could not move {legacy} to {new} ({e.strerror or e}); close anything using it "
            "(a running llama-server, a file explorer window), then run `enjrorun migrate`"
        ) from e
    _rewrite_json_paths(new, legacy)
    _rename_db(new)
    try:
        legacy.parent.rmdir()  # ~/.strix, only if nothing else (a sister app) uses it
    except OSError:
        pass
    return f"Moved your models and settings from {legacy} to {new}."


# ---- login service -----------------------------------------------------------------------


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False, **hidden())


def _legacy_launchd_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LEGACY_LABEL}.plist"


def _legacy_systemd_unit() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / LEGACY_UNIT_NAME


def _platform() -> str:
    return "darwin" if sys.platform == "darwin" else "windows" if os.name == "nt" else "linux"


def legacy_service_present() -> bool:
    """Cheap check (no subprocess): a StrixRun launchd agent or systemd unit file exists.

    Windows tasks aren't checked here; the installer runs `enjrorun migrate` itself.
    """
    plat = _platform()
    if plat == "darwin":
        return _legacy_launchd_plist().exists()
    return plat == "linux" and _legacy_systemd_unit().exists()


def remove_legacy_service() -> bool:
    """Remove a StrixRun login service if one is installed. True if one was."""
    plat = _platform()
    if plat == "darwin":
        plist = _legacy_launchd_plist()
        if not plist.exists():
            return False
        _run(["launchctl", "bootout", f"gui/{os.getuid()}/{LEGACY_LABEL}"])
        plist.unlink(missing_ok=True)
        return True
    if plat == "windows":
        if _run(["schtasks", "/Query", "/TN", LEGACY_TASK_NAME]).returncode != 0:
            return False
        _run(["schtasks", "/End", "/TN", LEGACY_TASK_NAME])
        _run(["schtasks", "/Delete", "/TN", LEGACY_TASK_NAME, "/F"])
        return True
    unit = _legacy_systemd_unit()
    if not unit.exists():
        return False
    _run(["systemctl", "--user", "disable", "--now", LEGACY_UNIT_NAME])
    unit.unlink(missing_ok=True)
    _run(["systemctl", "--user", "daemon-reload"])
    return True


def migrate_service() -> str | None:
    if not remove_legacy_service():
        return None
    try:
        return "Replaced the StrixRun login service. " + service.install()
    except service.ServiceError as e:
        return f"Removed the StrixRun login service, but installing Enjro Run's failed: {e}"


def migrate_all() -> list[str]:
    """Data first (the new service must point at the moved folder). Raises MigrationBlocked."""
    return [m for m in (migrate_data(), migrate_service()) if m]
