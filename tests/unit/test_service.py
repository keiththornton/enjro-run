import plistlib
import subprocess

from strixrun import service

CMD = ["/Users/me/.local/bin/strixrun", "serve", "--log-file", "/Users/me/.strix/run/logs/serve.log"]


def test_launchd_plist_is_valid_and_complete(tmp_path):
    text = service.render_launchd(CMD, {"STRIX_HOME": "/Volumes/SSD/strix & co"}, tmp_path / "serve.log")
    d = plistlib.loads(text.encode())
    assert d["Label"] == service.LABEL
    assert d["ProgramArguments"] == CMD
    assert d["RunAtLoad"] is True and d["KeepAlive"] == {"SuccessfulExit": False}
    assert d["EnvironmentVariables"] == {"STRIX_HOME": "/Volumes/SSD/strix & co"}
    assert d["StandardErrorPath"].endswith("serve.log")


def test_systemd_unit_quotes_paths_with_spaces():
    text = service.render_systemd(["/opt/my apps/strixrun", "serve"], {"STRIX_HOME": "/data/strix"})
    assert 'ExecStart="/opt/my apps/strixrun" serve' in text
    assert 'Environment="STRIX_HOME=/data/strix"' in text
    assert "Restart=on-failure" in text and "WantedBy=default.target" in text


def test_windows_task_command():
    cmd = service.windows_task_command([r"C:\Py\pythonw.exe", "-m", "strixrun", "serve"], {})
    assert cmd[:3] == ["schtasks", "/Create", "/TN"] and "/SC" in cmd and "ONLOGON" in cmd
    tr = cmd[cmd.index("/TR") + 1]
    assert tr == subprocess.list2cmdline([r"C:\Py\pythonw.exe", "-m", "strixrun", "serve"])
    with_env = service.windows_task_command(["x.exe"], {"STRIX_HOME": r"D:\strix"})
    assert 'set "STRIX_HOME=D:\\strix"' in with_env[with_env.index("/TR") + 1]


def test_serve_command_logs_to_file(monkeypatch, tmp_path):
    monkeypatch.setenv("STRIX_HOME", str(tmp_path))
    cmd = service.serve_command()
    assert cmd[-3:] == ["serve", "--log-file", str(tmp_path / "run" / "logs" / "serve.log")]
