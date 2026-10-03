"""AppContext: the set of core components, built once in the app lifespan.

Routes get it via `Depends(get_context)`. Core modules take their collaborators as
constructor arguments and never import `app` or `api/*`.
"""

from dataclasses import dataclass
from typing import Any

from strixrun.events import EventBus


@dataclass
class AppContext:
    bus: EventBus
    settings: Any = None  # SettingsStore
    registry: Any = None
    presets: Any = None
    backend: Any = None
    proxy: Any = None
    requestlog: Any = None
    downloads: Any = None
    binaries: Any = None
    metrics: Any = None
    estimator: Any = None  # MemoryEstimator; None until wired (load skips the check)
