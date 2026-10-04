"""Per-model load settings, plus how they become llama-server arguments.

`extra_args` is an allowlist: only performance and sampling flags are accepted.
Anything that reads or writes files, rebinds the server or changes its auth is
rejected, because presets can be set over the admin API.
"""

import json
import os
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

ID_RE = re.compile(r"^[a-z0-9._-]+$")
VALUE_RE = re.compile(r"^[A-Za-z0-9.,_:+-]{1,64}$")

# flag -> takes a value?
ALLOWED_EXTRA_FLAGS: dict[str, bool] = {
    "-t": True, "--threads": True,
    "-tb": True, "--threads-batch": True,
    "-b": True, "--batch-size": True,
    "-ub": True, "--ubatch-size": True,
    "--rope-scaling": True, "--rope-scale": True,
    "--rope-freq-base": True, "--rope-freq-scale": True,
    "--yarn-orig-ctx": True, "--yarn-ext-factor": True,
    "--no-mmap": False, "--mlock": False,
    "--defrag-thold": True, "--kv-unified": False,
    "--cont-batching": False, "--no-cont-batching": False,
    "--no-context-shift": False, "--cache-reuse": True,
    "-n": True, "--n-predict": True,
    "--temp": True, "--top-k": True, "--top-p": True, "--min-p": True,
    "--repeat-penalty": True, "--seed": True,
    "--reasoning-format": True, "--reasoning-budget": True,
    "-sm": True, "--split-mode": True,
    "-ts": True, "--tensor-split": True,
    "-mg": True, "--main-gpu": True,
}

CACHE_TYPES = {"f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1"}
DEFAULT_MAX_CTX = 8192


class PresetError(ValueError):
    pass


@dataclass
class Preset:
    ctx_per_request: int = DEFAULT_MAX_CTX
    n_gpu_layers: int = -1  # -1 = auto: llama.cpp (--fit) puts what fits on the GPU, the rest on the CPU
    parallel: int = 2
    queue_max: int = 16
    flash_attn: str = "auto"
    cache_type_k: str = "f16"
    cache_type_v: str = "f16"
    mode: str = "chat"
    # One KV pool shared by all slots: a single conversation can use the whole context,
    # and memory is 1x the context instead of 1x per slot.
    kv_unified: bool = True
    chat_template: str | None = None
    extra_args: list[str] = field(default_factory=list)
    sampling: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "Preset":
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in data.items() if k in known}
        if data.get("n_gpu_layers") == 999:
            data["n_gpu_layers"] = -1  # older StrixRun's "all layers" default; auto now does better
        p = cls(**data)
        p.validate()
        return p

    def to_dict(self) -> dict:
        return asdict(self)

    def validate(self) -> None:
        if not 128 <= self.ctx_per_request <= 1_048_576:
            raise PresetError("ctx_per_request must be between 128 and 1048576")
        if not 1 <= self.parallel <= 64:
            raise PresetError("parallel must be between 1 and 64")
        if not 0 <= self.queue_max <= 10_000:
            raise PresetError("queue_max must be between 0 and 10000")
        if self.n_gpu_layers < -1:
            raise PresetError("n_gpu_layers must be -1 (auto), 0 (CPU only) or a layer count")
        if self.flash_attn not in {"on", "off", "auto"}:
            raise PresetError("flash_attn must be on, off or auto")
        if self.cache_type_k not in CACHE_TYPES or self.cache_type_v not in CACHE_TYPES:
            raise PresetError(f"cache types must be one of {sorted(CACHE_TYPES)}")
        if self.cache_type_v not in {"f16", "f32", "bf16"} and self.flash_attn == "off":
            raise PresetError("a quantized V cache requires flash attention (on or auto)")
        if self.mode not in {"chat", "embedding"}:
            raise PresetError("mode must be chat or embedding")
        _validate_extra_args(self.extra_args)


def _validate_extra_args(args: list[str]) -> None:
    i = 0
    while i < len(args):
        flag = args[i]
        if flag not in ALLOWED_EXTRA_FLAGS:
            raise PresetError(f"extra argument {flag!r} is not allowed")
        if ALLOWED_EXTRA_FLAGS[flag]:
            if i + 1 >= len(args):
                raise PresetError(f"{flag} needs a value")
            value = args[i + 1]
            if not VALUE_RE.match(value) or value.startswith("--"):
                raise PresetError(f"invalid value {value!r} for {flag}")
            i += 2
        else:
            i += 1


def default_preset(meta: dict) -> Preset:
    trained = meta.get("context_length") or DEFAULT_MAX_CTX
    return Preset(
        ctx_per_request=min(int(trained), DEFAULT_MAX_CTX),
        mode="embedding" if meta.get("embedding_only") else "chat",
    )


def resolve_template(name: str, templates_dir: Path) -> Path:
    if os.path.isabs(name) or "\\" in name or ":" in name or ".." in Path(name).parts:
        raise PresetError("chat_template must be a file name inside the templates folder")
    root = Path(templates_dir).resolve()
    path = (root / name).resolve()
    if root not in path.parents:
        raise PresetError("chat_template must be a file name inside the templates folder")
    if not path.is_file():
        raise PresetError(f"chat template {name!r} not found in {root}")
    return path


def llama_args(preset: Preset, templates_dir: Path) -> list[str]:
    """llama-server arguments for a preset (excluding -m/--host/--port/--api-key)."""
    preset.validate()
    args = [
        "--jinja",
        "-c", str(preset.ctx_per_request if preset.kv_unified else preset.ctx_per_request * preset.parallel),
        "--kv-unified" if preset.kv_unified else "--no-kv-unified",
        "-lv", "4",  # log layer placement ("offloaded X/Y layers to GPU") and --fit's decisions
        "--parallel", str(preset.parallel),
        "--flash-attn", preset.flash_attn,
        "-ctk", preset.cache_type_k,
        "-ctv", preset.cache_type_v,
    ]
    if preset.n_gpu_layers >= 0:
        args += ["-ngl", str(preset.n_gpu_layers)]  # unset (auto) lets llama.cpp's --fit decide
    if preset.mode == "embedding":
        args.append("--embeddings")
    if preset.chat_template:
        args += ["--chat-template-file", str(resolve_template(preset.chat_template, templates_dir))]
    return args + list(preset.extra_args)


class PresetStore:
    def __init__(self, directory: Path) -> None:
        self.dir = Path(directory)

    def _path(self, model_id: str) -> Path:
        if not ID_RE.match(model_id) or model_id in {".", ".."}:
            raise PresetError(f"unsafe model id {model_id!r}")
        return self.dir / f"{model_id}.json"

    def load(self, model_id: str, meta: dict) -> Preset:
        path = self._path(model_id)
        if path.exists():
            return Preset.from_dict(json.loads(path.read_text()))
        return default_preset(meta)

    def save(self, model_id: str, preset: Preset) -> None:
        path = self._path(model_id)
        preset.validate()
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(preset.to_dict(), indent=2))
        os.replace(tmp, path)

    def delete(self, model_id: str) -> None:
        self._path(model_id).unlink(missing_ok=True)
