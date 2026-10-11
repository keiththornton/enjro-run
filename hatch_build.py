"""Hatch build hook: build the React dashboard into src/enjrorun/web_dist.

Skipped when web_dist is already built (e.g. CI built it first), or when web/ isn't
present (building from an sdist without the dashboard sources).

Without npm, a wheel build fails rather than producing an Enjro Run with no dashboard
(set ENJRORUN_NO_DASHBOARD=1 to allow that). Editable installs for development only
warn, so Python-only work doesn't need Node.
"""

import os
import shutil
import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class DashboardBuildHook(BuildHookInterface):
    PLUGIN_NAME = "dashboard"

    def initialize(self, version, build_data):
        root = Path(self.root)
        dist = root / "src" / "enjrorun" / "web_dist" / "index.html"
        web = root / "web"
        if dist.exists() or not (web / "package.json").exists():
            return
        npm = shutil.which("npm")
        if npm is None:
            if version == "editable" or os.environ.get("ENJRORUN_NO_DASHBOARD"):
                self.app.display_warning("npm not found: building Enjro Run without the dashboard")
                return
            raise RuntimeError(
                "Building Enjro Run from source needs Node 20+ (npm) for the dashboard. Install Node, "
                "or install a release instead: installer/install.sh downloads a prebuilt package. "
                "Set ENJRORUN_NO_DASHBOARD=1 to build without the dashboard."
            )
        self.app.display_info("building the Enjro Run dashboard (npm ci && npm run build)")
        subprocess.run([npm, "ci"], cwd=web, check=True)
        subprocess.run([npm, "run", "build"], cwd=web, check=True)
