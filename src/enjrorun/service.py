"""Run Enjro Run at login: launchd (macOS), Task Scheduler (Windows), systemd --user (Linux)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from enjrorun import paths
from enjrorun.winproc import hidden

LABEL = "com.enjrorun.serve"
TASK_NAME = "EnjroRun"
UNIT_NAME = "enjrorun.service"


class ServiceError(RuntimeError):
    pass


def serve_command() -> list[str]:
    """How to start `enjrorun serve` without a console: the installed script, else python -m."""
    exe = shutil.which("enjrorun")
    if os.name == "nt":
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        base = [str(pythonw if pythonw.exists() else sys.executable), "-m", "enjrorun"]
    else:
        base = [exe] if exe else [sys.executable, "-m", "enjrorun"]
    return [*base, "serve", "--log-file", str(log_path())]


def log_path() -> Path:
    return paths.run_dir() / "logs" / "serve.log"


def service_env() -> dict[str, str]:
    custom = os.environ.get("ENJRO_HOME") or os.environ.get("STRIX_HOME")
    return {"ENJRO_HOME": custom} if custom else {}


# ---- file generators (pure; unit-tested) ------------------------------------------------


def render_launchd(cmd: list[str], env: dict[str, str], log: Path) -> str:
    args = "\n".join(f"    <string>{escape(a)}</string>" for a in cmd)
    env_xml = "".join(f"\n    <key>{escape(k)}</key>\n    <string>{escape(v)}</string>" for k, v in env.items())
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
{args}
  </array>
  <key>EnvironmentVariables</key>
  <dict>{env_xml}
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>StandardOutPath</key>
  <string>{escape(str(log))}</string>
  <key>StandardErrorPath</key>
  <string>{escape(str(log))}</string>
</dict>
</plist>
"""


def render_systemd(cmd: list[str], env: dict[str, str]) -> str:
    def quote(a: str) -> str:
        return '"' + a.replace("\\", "\\\\").replace('"', '\\"') + '"' if any(c in a for c in ' "\\') else a

    env_lines = "".join(f'Environment="{k}={v}"\n' for k, v in env.items())
    return f"""[Unit]
Description=Enjro Run local LLM server
After=network-online.target

[Service]
ExecStart={" ".join(quote(a) for a in cmd)}
{env_lines}Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


def windows_task_command(cmd: list[str], env: dict[str, str]) -> list[str]:
    run = subprocess.list2cmdline(cmd)
    if env:
        sets = " && ".join(f'set "{k}={v}"' for k, v in env.items())
        run = f'cmd /c "{sets} && {run}"'
    return ["schtasks", "/Create", "/TN", TASK_NAME, "/TR", run, "/SC", "ONLOGON", "/RL", "LIMITED", "/F"]


# ---- install / uninstall ----------------------------------------------------------------


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True, check=False, **hidden())
    if check and r.returncode != 0:
        raise ServiceError(f"{' '.join(cmd[:3])} failed: {(r.stderr or r.stdout).strip()}")
    return r


def launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / UNIT_NAME


def _platform() -> str:
    return "darwin" if sys.platform == "darwin" else "windows" if os.name == "nt" else "linux"


def install() -> str:
    cmd, env, log = serve_command(), service_env(), log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    if _platform() == "darwin":
        plist = launchd_plist_path()
        plist.parent.mkdir(parents=True, exist_ok=True)
        domain = f"gui/{os.getuid()}"
        _run(["launchctl", "bootout", f"{domain}/{LABEL}"], check=False)  # replace an old install
        plist.write_text(render_launchd(cmd, env, log))
        _run(["launchctl", "bootstrap", domain, str(plist)])
        return f"Installed launchd agent {LABEL}; Enjro Run starts at login (log: {log})."
    if _platform() == "windows":
        # Not started now: the installer (or `enjrorun open`) starts Enjro Run itself, and
        # running the task as well raced it with a second server.
        _run(windows_task_command(cmd, env))
        return (f"Installed Task Scheduler task {TASK_NAME}; Enjro Run starts when you sign in "
                f"(log: {log}). Run `enjrorun open` to start it now.")
    unit = systemd_unit_path()
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(render_systemd(cmd, env))
    _run(["systemctl", "--user", "daemon-reload"])
    _run(["systemctl", "--user", "enable", "--now", UNIT_NAME])
    return f"Installed systemd user unit {UNIT_NAME} (log: journalctl --user -u enjrorun)."


def uninstall() -> str:
    if _platform() == "darwin":
        _run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], check=False)
        launchd_plist_path().unlink(missing_ok=True)
        return "Removed the launchd agent; Enjro Run no longer starts at login."
    if _platform() == "windows":
        _run(["schtasks", "/End", "/TN", TASK_NAME], check=False)
        _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], check=False)
        return "Removed the Task Scheduler task."
    _run(["systemctl", "--user", "disable", "--now", UNIT_NAME], check=False)
    systemd_unit_path().unlink(missing_ok=True)
    _run(["systemctl", "--user", "daemon-reload"], check=False)
    return "Removed the systemd user unit."
