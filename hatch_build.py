"""Hatch build hook: build the React dashboard into src/strixrun/web_dist.

Skipped when web_dist is already built (e.g. CI built it first), or when web/ isn't
present (building from an sdist without the dashboard sources).
"""

import shutil
import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class DashboardBuildHook(BuildHookInterface):
    PLUGIN_NAME = "dashboard"

    def initialize(self, version, build_data):
        root = Path(self.root)
        dist = root / "src" / "strixrun" / "web_dist" / "index.html"
        web = root / "web"
        if dist.exists() or not (web / "package.json").exists():
            return
        npm = shutil.which("npm")
        if npm is None:
            self.app.display_warning("npm not found: building StrixRun without the dashboard")
            return
        self.app.display_info("building the StrixRun dashboard (npm ci && npm run build)")
        subprocess.run([npm, "ci"], cwd=web, check=True)
        subprocess.run([npm, "run", "build"], cwd=web, check=True)
