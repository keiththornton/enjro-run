"""Check that every asset URL in the binary manifest still resolves (weekly CI job).

Also reports whether the newest upstream build still uses the asset names we expect,
so a naming change shows up before we try to pin a new tag.
"""

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pin_binary import REPO, VARIANTS

MANIFEST = Path(__file__).resolve().parents[1] / "src" / "strixrun" / "binaries" / "manifest.json"


def head_ok(url: str) -> bool:
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status == 200
    except OSError:
        return False


def main() -> int:
    manifest = json.loads(MANIFEST.read_text())
    failures = 0
    for tag, variants in manifest["tags"].items():
        for variant, e in variants.items():
            for item in [e, *e.get("extras", [])]:
                ok = head_ok(item["url"])
                failures += not ok
                print(f"{'ok  ' if ok else 'FAIL'} {tag} {variant}: {item['asset']}")

    releases = json.loads(subprocess.run(
        ["gh", "api", f"repos/{REPO}/releases?per_page=5"], check=True, capture_output=True, text=True
    ).stdout)
    newest = next((r for r in releases if r["tag_name"].startswith("b")), None)
    if newest:
        names = {a["name"] for a in newest["assets"]}
        missing = [t.format(tag=newest["tag_name"]) for t, _ in VARIANTS.values()
                   if t.format(tag=newest["tag_name"]) not in names]
        print(f"newest build {newest['tag_name']}: "
              + ("asset names unchanged" if not missing else f"MISSING {missing}"))
        failures += bool(missing)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
