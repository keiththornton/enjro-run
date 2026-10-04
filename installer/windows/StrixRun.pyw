"""Start Menu launcher: run `strixrun <command>` with no console, and never fail silently.

pythonw has no console, so an error while importing or starting StrixRun would
otherwise vanish without a trace. This file only uses the standard library until
StrixRun is imported, and any failure is shown in a message box and written to
launch-error.log next to this file.
"""

import sys
import traceback
from pathlib import Path


def _report(message: str) -> None:
    log = Path(__file__).with_name("launch-error.log")
    try:
        log.write_text(message, encoding="utf-8")
    except OSError:
        pass
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            "StrixRun couldn't start.\n\n" + message[-1500:] +
            f"\n\nDetails were saved to:\n{log}\n\nRunning the StrixRun installer again usually fixes this.",
            "StrixRun", 0x10,
        )
    except Exception:  # noqa: BLE001 - nothing more we can do without a console
        pass


def main() -> None:
    try:
        from strixrun.cli import app
    except BaseException:  # noqa: BLE001 - report anything, including a broken install
        _report(traceback.format_exc())
        return
    sys.argv = ["strixrun", *sys.argv[1:]]
    try:
        app()
    except SystemExit:
        raise  # strixrun reports its own errors (open/stop show a message box)
    except BaseException:  # noqa: BLE001
        _report(traceback.format_exc())


if __name__ == "__main__":
    main()
