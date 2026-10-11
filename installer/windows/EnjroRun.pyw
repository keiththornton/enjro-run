"""Start Menu launcher: run `enjrorun <command>` with no console, and never fail silently.

pythonw has no console, so an error while importing or starting Enjro Run would
otherwise vanish without a trace. This file only uses the standard library until
Enjro Run is imported, and any failure is shown in a message box and written to
launch-error.log next to this file.
"""

import os
import sys
import traceback
from pathlib import Path


def _report(message: str) -> None:
    log = Path(__file__).with_name("launch-error.log")
    try:
        log.write_text(message, encoding="utf-8")
    except OSError:
        pass
    if os.environ.get("ENJRORUN_NO_DIALOG"):  # tests: nobody is there to close the box
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            "Enjro Run couldn't start.\n\n" + message[-1500:] +
            f"\n\nDetails were saved to:\n{log}\n\nRunning the Enjro Run installer again usually fixes this.",
            "Enjro Run", 0x10,
        )
    except Exception:  # noqa: BLE001 - nothing more we can do without a console
        pass


def main() -> None:
    try:
        from enjrorun.cli import app
    except BaseException:  # noqa: BLE001 - report anything, including a broken install
        _report(traceback.format_exc())
        return
    sys.argv = ["enjrorun", *sys.argv[1:]]
    try:
        app()
    except SystemExit:
        raise  # enjrorun reports its own errors (open/stop show a message box)
    except BaseException:  # noqa: BLE001
        _report(traceback.format_exc())


if __name__ == "__main__":
    main()
