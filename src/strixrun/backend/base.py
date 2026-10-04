"""The Backend protocol: everything above the process layer talks only to this."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from strixrun.presets import Preset
from strixrun.registry import ModelEntry


class ModelStatus(StrEnum):
    LOADING = "loading"
    READY = "ready"
    STOPPING = "stopping"
    STOPPED = "stopped"
    CRASHED = "crashed"


@dataclass
class ModelState:
    status: ModelStatus = ModelStatus.STOPPED
    reason: str | None = None
    crash_class: str | None = None
    pid: int | None = None
    port: int | None = None
    started_at: float | None = None
    ready_at: float | None = None
    gpu_layers: tuple[int, int] | None = None  # (on GPU, total) from llama.cpp's load log
    # What the running server accepts besides text, from llama-server's /props:
    # {"vision": bool, "audio": bool, "video": bool}. None until ready.
    modalities: dict[str, bool] | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "reason": self.reason,
            "crash_class": self.crash_class,
            "pid": self.pid,
            "port": self.port,
            "started_at": self.started_at,
            "ready_at": self.ready_at,
            "gpu_layers": list(self.gpu_layers) if self.gpu_layers else None,
            "modalities": self.modalities,
        }

    def accepts(self, modality: str) -> bool:
        return bool(self.modalities and self.modalities.get(modality))


@dataclass(frozen=True)
class Target:
    base_url: str
    upstream_model_name: str
    headers: dict[str, str] = field(default_factory=dict)


class Backend(Protocol):
    async def load(self, model: ModelEntry, preset: Preset) -> None: ...
    async def unload(self, model_id: str) -> None: ...
    async def wait_settled(self, model_id: str) -> ModelState: ...
    def state(self, model_id: str) -> ModelState: ...
    def target(self, model_id: str) -> Target: ...
    def logs(self, model_id: str) -> list[str]: ...
    def loaded(self) -> list[str]: ...
    async def shutdown(self) -> None: ...
