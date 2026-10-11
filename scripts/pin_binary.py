"""Pin a llama.cpp release tag into src/enjrorun/binaries/manifest.json.

Usage: uv run python scripts/pin_binary.py <tag> [--default] [--verify-small]

Checksums come from GitHub's published asset digests. With --verify-small, the
small archives (CPU and macOS builds) are downloaded and hashed to cross-check those
digests. Enjro Run verifies every download against this manifest at install time.
"""

import argparse
import hashlib
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

MANIFEST = Path(__file__).resolve().parents[1] / "src" / "enjrorun" / "binaries" / "manifest.json"
REPO = "ggml-org/llama.cpp"
SMALL = 64 * 1024 * 1024

# variant -> (asset name template, extra asset templates)
VARIANTS = {
    "macos-arm64": ("llama-{tag}-bin-macos-arm64.tar.gz", []),
    "macos-x64": ("llama-{tag}-bin-macos-x64.tar.gz", []),
    "win-cpu-x64": ("llama-{tag}-bin-win-cpu-x64.zip", []),
    "win-cuda-12-x64": ("llama-{tag}-bin-win-cuda-12.4-x64.zip", ["cudart-llama-bin-win-cuda-12.4-x64.zip"]),
    "win-cuda-13-x64": ("llama-{tag}-bin-win-cuda-13.4-x64.zip", ["cudart-llama-bin-win-cuda-13.4-x64.zip"]),
    "linux-cpu-x64": ("llama-{tag}-bin-ubuntu-x64.tar.gz", []),
    "linux-cuda-12-x64": ("llama-{tag}-bin-ubuntu-cuda-12.8-x64.tar.gz",
                          ["cudart-llama-{tag}-bin-ubuntu-cuda-12.8-x64.tar.gz"]),
    "linux-cuda-13-x64": ("llama-{tag}-bin-ubuntu-cuda-13.4-x64.tar.gz",
                          ["cudart-llama-{tag}-bin-ubuntu-cuda-13.4-x64.tar.gz"]),
}


def release_assets(tag: str) -> dict[str, dict]:
    out = subprocess.run(["gh", "api", f"repos/{REPO}/releases/tags/{tag}"], check=True,
                         capture_output=True, text=True).stdout
    return {a["name"]: a for a in json.loads(out)["assets"]}


def entry(asset: dict) -> dict:
    digest = asset.get("digest") or ""
    if not digest.startswith("sha256:"):
        sys.exit(f"{asset['name']}: GitHub published no sha256 digest; hash it manually")
    name = asset["name"]
    return {
        "asset": name,
        "url": asset["browser_download_url"],
        "format": "zip" if name.endswith(".zip") else "tar.gz",
        "size": asset["size"],
        "sha256": digest.removeprefix("sha256:"),
    }


def verify(e: dict) -> None:
    h = hashlib.sha256()
    with urllib.request.urlopen(e["url"]) as r:
        while chunk := r.read(1 << 20):
            h.update(chunk)
    if h.hexdigest() != e["sha256"]:
        sys.exit(f"{e['asset']}: downloaded sha256 {h.hexdigest()} != published {e['sha256']}")
    print(f"verified {e['asset']}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("tag")
    p.add_argument("--default", action="store_true", help="make this the default tag")
    p.add_argument("--verify-small", action="store_true")
    args = p.parse_args()

    assets = release_assets(args.tag)
    variants = {}
    for variant, (template, extras) in VARIANTS.items():
        name = template.format(tag=args.tag)
        if name not in assets:
            sys.exit(f"{args.tag}: missing asset {name} (upstream naming changed?)")
        variants[variant] = {**entry(assets[name]), "extras": [entry(assets[x.format(tag=args.tag)]) for x in extras]}
        if args.verify_small:
            for e in [variants[variant], *variants[variant]["extras"]]:
                if e["size"] <= SMALL:
                    verify(e)

    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {"default": None, "tags": {}}
    manifest["tags"][args.tag] = variants
    if args.default or manifest["default"] is None:
        manifest["default"] = args.tag
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"pinned {args.tag} ({len(variants)} variants); default={manifest['default']}")


if __name__ == "__main__":
    main()
