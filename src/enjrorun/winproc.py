"""Windows: start child processes without console windows.

Enjro Run often runs with no console of its own (pythonw, from the Start Menu or the
sign-in task). On Windows a console program started from such a process gets a new,
visible console window unless CREATE_NO_WINDOW is passed, so every child process
goes through hidden().
"""

import os
import subprocess


def hidden(extra: int = 0) -> dict:
    """subprocess keyword arguments that keep a child's console window hidden on Windows."""
    if os.name != "nt":
        return {}
    return {"creationflags": subprocess.CREATE_NO_WINDOW | extra}
