"""installer/windows/StrixRun.pyw: the Start Menu launcher must never fail silently."""

import shutil
import subprocess
import sys
from pathlib import Path

LAUNCHER = Path(__file__).resolve().parents[2] / "installer" / "windows" / "StrixRun.pyw"


def test_runs_strixrun_commands(tmp_path):
    copy = tmp_path / "StrixRun.pyw"
    shutil.copy(LAUNCHER, copy)
    r = subprocess.run([sys.executable, str(copy), "version"], capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0 and r.stdout.strip()
    assert not (tmp_path / "launch-error.log").exists()


def test_broken_install_is_reported_not_silent(tmp_path):
    copy = tmp_path / "StrixRun.pyw"
    shutil.copy(LAUNCHER, copy)
    broken = tmp_path / "site" / "strixrun"
    broken.mkdir(parents=True)
    (broken / "__init__.py").write_text("raise ImportError('half-replaced install')\n")
    (broken / "cli.py").write_text("")
    r = subprocess.run([sys.executable, str(copy), "open"], capture_output=True, text=True, timeout=60, check=False,
                       env={"PYTHONPATH": str(tmp_path / "site"), "PATH": ""})
    log = tmp_path / "launch-error.log"
    assert log.exists(), r.stderr
    assert "half-replaced install" in log.read_text()
