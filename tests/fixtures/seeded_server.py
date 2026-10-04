"""A StrixRun server for UI tests: fake llama-server children and one pre-registered model.

Usage: python tests/fixtures/seeded_server.py --port 18799
Uses a fresh temporary STRIX_HOME on every start; serves the built dashboard.
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FAKE = str(ROOT / "tests" / "fake_llama_server.py")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=18799)
    args = p.parse_args()

    home = tempfile.mkdtemp(prefix="strixrun-ui-")
    os.environ["STRIX_HOME"] = home
    os.environ.setdefault("FAKE_TOKEN_DELAY", "0.03")

    import uvicorn

    from strixrun import paths
    from strixrun.app import create_app
    from strixrun.registry import ModelEntry, Registry
    from strixrun.settings import SettingsStore

    store = SettingsStore()
    s = store.load()
    s.port = args.port
    store.save(s)
    model = Path(home) / "fake-model.gguf"
    model.write_bytes(b"GGUF")
    Registry(paths.registry_path()).add(ModelEntry(
        id="test--tiny-chat--q4_k_m", aliases=["tiny-chat:q4_k_m"], paths=[str(model)],
        size_bytes=400_000_000, repo="test/tiny-chat-GGUF", quant="Q4_K_M",
        meta={"architecture": "llama", "context_length": 8192, "n_layer": 16, "n_head": 16,
              "n_head_kv": 4, "head_dim_k": 64, "head_dim_v": 64},
    ))
    class OfflineHub:
        """Stand-in for Hugging Face so UI tests never depend on live data."""

        def list_gguf(self, sort, limit):
            return [
                {"repo": "bartowski/Llama-3.2-3B-Instruct-GGUF", "downloads": 912_000, "likes": 140,
                 "trending_score": 40, "pipeline_tag": "text-generation", "created_at": "2026-09-30T00:00:00+00:00",
                 "last_modified": None, "gated": False},
                {"repo": "unsloth/Qwen3.8-27B-GGUF", "downloads": 6_300_000, "likes": 4800, "trending_score": 120,
                 "pipeline_tag": None, "created_at": "2026-08-13T00:00:00+00:00", "last_modified": None, "gated": False},
            ]

        def search(self, query, limit):
            return []

        def repo_files(self, repo):
            return {"gated": False, "files": []}

    app = create_app(store, binary=lambda: [sys.executable, FAKE], load_timeout=30, hub=OfflineHub())
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
