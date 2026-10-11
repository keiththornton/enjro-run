"""Every child process must be started without a console window on Windows.

Enjro Run usually runs under pythonw (Start Menu shortcut, sign-in task), which has no
console; on Windows any console program it starts then gets a new window of its own
unless CREATE_NO_WINDOW is passed. Users saw a window flash for every nvidia-smi call.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "enjrorun"
LAUNCHERS = {"run", "Popen", "check_output", "call", "create_subprocess_exec", "create_subprocess_shell"}
MAC_ONLY = {("binaries/manager.py", "xattr")}  # macOS-only commands


def _launch_calls(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            owner = f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else None
            if name in LAUNCHERS and owner in ("subprocess", "asyncio"):
                yield node
        # asyncio.to_thread(subprocess.run, [...], ...) passes the launcher as an argument
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "to_thread" and node.args:
            a = node.args[0]
            if isinstance(a, ast.Attribute) and a.attr in LAUNCHERS:
                yield node


def _hides_window(call: ast.Call, source: str) -> bool:
    for kw in call.keywords:
        if kw.arg == "creationflags" and "CREATE_NO_WINDOW" in ast.unparse(kw.value):
            return True
        if kw.arg is None:
            expr = ast.unparse(kw.value)
            if "hidden(" in expr:
                return True
            # a kwargs dict counts only if this module builds one from hidden() / CREATE_NO_WINDOW
            if expr == "kwargs" and ("hidden(" in source or "CREATE_NO_WINDOW" in source):
                return True
    return False


def test_every_child_process_hides_its_console_window():
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        source = path.read_text()
        for call in _launch_calls(ast.parse(source)):
            src = ast.unparse(call)
            if any(rel == f and cmd in src for f, cmd in MAC_ONLY):
                continue
            if not _hides_window(call, source):
                offenders.append(f"{rel}:{call.lineno}: {src[:80]}")
    assert not offenders, "launch without CREATE_NO_WINDOW on Windows:\n" + "\n".join(offenders)


def test_hidden_flags():
    import os
    import subprocess

    from enjrorun.winproc import hidden

    flags = hidden().get("creationflags", 0)
    if os.name == "nt":
        assert flags & subprocess.CREATE_NO_WINDOW
    else:
        assert hidden() == {}
    assert hidden(extra=0x200).get("creationflags", 0) & 0x200 or os.name != "nt"
