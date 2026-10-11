"""Download one Hugging Face file in a separate, killable process.

Usage: python -m enjrorun.hf_worker <repo> <filename> <local_dir> [--fake-size N]

Prints one JSON line per progress update ({"done", "total"}), then
{"path": ...} on success or {"error": ...} on failure. The parent cancels by
stopping the process: the Xet download backend ignores exceptions raised from
progress callbacks, so in-process cancellation isn't possible.
"""

import json
import sys
import time

PROGRESS_MIN_INTERVAL_S = 0.25


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def make_tqdm(total_hint: int | None):
    from tqdm.auto import tqdm

    state = {"bars": {}, "last": 0.0}

    class Progress(tqdm):
        def __init__(self, *a, **kw):
            kw["disable"] = True  # no terminal output; note a disabled bar doesn't advance self.n
            super().__init__(*a, **kw)
            self.count = 0
            state["bars"][id(self)] = 0

        def update(self, n=1):
            super().update(n)
            self.count += n or 0
            # Xet reports two bars (bytes downloaded, file reconstructed): show the leader.
            state["bars"][id(self)] = self.count
            now = time.monotonic()
            if now - state["last"] >= PROGRESS_MIN_INTERVAL_S:
                state["last"] = now
                emit({"done": max(state["bars"].values()), "total": self.total or total_hint})

    return Progress


def fake_download(size: int) -> None:
    step = max(1, size // 20)
    for done in range(step, size + 1, step):
        time.sleep(0.05)
        emit({"done": done, "total": size})
    emit({"path": "fake"})


def main(argv: list[str]) -> int:
    if len(argv) >= 5 and argv[3] == "--fake-size":
        fake_download(int(argv[4]))
        return 0
    if len(argv) != 3:
        emit({"error": "usage: hf_worker <repo> <filename> <local_dir>"})
        return 2
    repo, filename, local_dir = argv
    try:
        from huggingface_hub import hf_hub_download

        path = hf_hub_download(repo, filename, local_dir=local_dir, tqdm_class=make_tqdm(None))
    except Exception as e:  # noqa: BLE001 - any failure is reported to the parent process
        emit({"error": f"{type(e).__name__}: {e}"})
        return 1
    emit({"path": path})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
