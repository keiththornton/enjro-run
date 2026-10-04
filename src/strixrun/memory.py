"""Memory estimate for loading a model with a preset, and what's available for it.

estimate = weights (GGUF file sizes) + KV cache + compute buffers (heuristic).
This is an estimate: it warns, it never blocks. Known gaps: recurrent/hybrid layers
are costed as attention (overestimate) and vision projectors aren't counted.
"""

import platform
import shutil
import subprocess

import psutil

from strixrun.presets import Preset
from strixrun.registry import ModelEntry
from strixrun.winproc import hidden

GiB = 1024**3
# bytes per element; quantized types include their block scales
CACHE_BYTES = {
    "f32": 4.0, "f16": 2.0, "bf16": 2.0,
    "q8_0": 34 / 32, "q4_0": 18 / 32, "q4_1": 20 / 32, "iq4_nl": 18 / 32,
    "q5_0": 22 / 32, "q5_1": 24 / 32,
}
METAL_DEFAULT_FRACTION = 0.67
TIGHT_FRACTION = 0.85


def _sysctl_int(name: str) -> int | None:
    try:
        out = subprocess.run(["sysctl", "-n", name], capture_output=True, text=True,
                             timeout=2, check=False, **hidden()).stdout.strip()
        return int(out)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


class SystemProbe:
    def total_ram(self) -> int:
        return psutil.virtual_memory().total

    def available_ram(self) -> int:
        vm = psutil.virtual_memory()
        if platform.system() == "Darwin":
            # psutil's "available" omits memory macOS readily reclaims (inactive, file
            # cache, compressible), badly understating headroom. The kernel's own
            # memory-pressure level is the free percentage macOS acts on.
            level = _sysctl_int("kern.memorystatus_level")
            if level is not None and 0 < level <= 100:
                return max(vm.available, vm.total * level // 100)
        return vm.available

    def is_apple_silicon(self) -> bool:
        return platform.system() == "Darwin" and platform.machine() == "arm64"

    def metal_wired_limit_mb(self) -> int | None:
        value = _sysctl_int("iogpu.wired_limit_mb")
        return value if value and value > 0 else None

    _gpu_cache: tuple[float, list[dict]] | None = None
    GPU_CACHE_S = 5.0

    def gpus(self) -> list[dict]:
        """NVIDIA GPUs with total and free memory (cached briefly: one search estimates many quants)."""
        import time

        now = time.monotonic()
        if self._gpu_cache and now - self._gpu_cache[0] < self.GPU_CACHE_S:
            return self._gpu_cache[1]
        value = self._query_gpus()
        self._gpu_cache = (now, value)
        return value

    def nvidia_free_bytes(self) -> int | None:
        """Free memory across all NVIDIA GPUs (llama.cpp splits layers over them), or None."""
        gpus = self.gpus()
        return sum(g["free_bytes"] for g in gpus) if gpus else None

    def _query_gpus(self) -> list[dict]:
        exe = shutil.which("nvidia-smi")
        if not exe:
            return []
        try:
            out = subprocess.run([exe, "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=5, check=False, **hidden()).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        gpus = []
        for line in out.splitlines():
            parts = line.rsplit(",", 2)
            if len(parts) != 3:
                continue
            try:
                total, free = int(parts[1].strip()), int(parts[2].strip())
            except ValueError:
                continue
            gpus.append({"name": parts[0].strip(), "total_bytes": total * 1024 * 1024, "free_bytes": free * 1024 * 1024})
        return gpus


def hybrid_attention_layers(meta: dict) -> int | None:
    """Number of layers with a KV cache in a hybrid model, if the metadata says."""
    if meta.get("n_attn_layers") is not None and meta.get("ssm_state_size"):
        return int(meta["n_attn_layers"])
    interval = meta.get("full_attention_interval")
    if interval and meta.get("n_layer"):
        return int(meta["n_layer"]) // int(interval)
    if meta.get("n_attn_layers") is not None and meta["n_attn_layers"] < (meta.get("n_layer") or 0):
        return int(meta["n_attn_layers"])
    return None


class MemoryEstimator:
    def __init__(self, probe=None) -> None:
        self.probe = probe or SystemProbe()

    def kv_bytes(self, meta: dict, preset: Preset) -> int:
        n_layer, n_head_kv = meta.get("n_layer"), meta.get("n_head_kv")
        dk, dv = meta.get("head_dim_k"), meta.get("head_dim_v")
        if not all((n_layer, n_head_kv, dk, dv)):
            return 0
        per_pos_layer = n_head_kv * (dk * CACHE_BYTES[preset.cache_type_k] + dv * CACHE_BYTES[preset.cache_type_v])
        n_ctx = preset.ctx_per_request * (1 if preset.kv_unified else preset.parallel)
        attn_layers = hybrid_attention_layers(meta)
        if attn_layers is not None:
            # Hybrid: only attention layers keep a KV cache; recurrent layers hold a small
            # fixed f32 state per sequence (inner size x state size).
            recurrent = (n_layer - attn_layers) * preset.parallel * (
                (meta.get("ssm_inner_size") or 0) * (meta.get("ssm_state_size") or 0) * 4
            )
            return int(per_pos_layer * attn_layers * n_ctx + recurrent)
        window, pattern = meta.get("sliding_window"), meta.get("sliding_window_pattern")
        if window and isinstance(pattern, int) and pattern > 1:
            n_global = n_layer // pattern
            n_swa = n_layer - n_global
            swa_ctx = min(n_ctx, window * preset.parallel)
            return int(per_pos_layer * (n_global * n_ctx + n_swa * swa_ctx))
        return int(per_pos_layer * n_layer * n_ctx)

    def available(self, preset: Preset) -> tuple[int, str]:
        """Memory a model loaded with `preset` can use, and which limit that is."""
        placement = self.place(0, preset)
        return placement["available_bytes"], placement["limit"]

    def place(self, total: int, preset: Preset, n_layer: int | None = None) -> dict:
        """Where llama.cpp will put `total` bytes, and whether that fits.

        NVIDIA: with GPU layers on auto, llama.cpp's --fit puts as many layers as fit in
        VRAM and runs the rest from system RAM on the CPU (slower), so capacity is VRAM
        plus RAM. Apple silicon shares one pool, capped by the Metal working-set limit.
        """
        ram = self.probe.available_ram()
        ngl = preset.n_gpu_layers
        vram = self.probe.nvidia_free_bytes() if ngl != 0 else None
        notes: list[str] = []

        def verdict(need: float, have: float) -> str:
            if need <= TIGHT_FRACTION * have:
                return "ok"
            return "tight" if need <= have else "over"

        if ngl != 0 and vram is None and self.probe.is_apple_silicon():
            limit_mb = self.probe.metal_wired_limit_mb()
            limit = limit_mb * 1024 * 1024 if limit_mb else int(METAL_DEFAULT_FRACTION * self.probe.total_ram())
            have = min(ram, limit)
            return {"placement": "gpu", "limit": "metal", "available_bytes": have, "spill_bytes": 0,
                    "verdict": verdict(total, have), "notes": notes, "vram_free_bytes": None}
        if vram is None:
            return {"placement": "cpu", "limit": "ram", "available_bytes": ram, "spill_bytes": 0,
                    "verdict": verdict(total, ram), "notes": notes, "vram_free_bytes": None}

        gpu_room = TIGHT_FRACTION * vram
        if ngl == -1 or (n_layer and ngl < n_layer):
            if ngl == -1 and total <= gpu_room:
                return {"placement": "gpu", "limit": "vram", "available_bytes": vram, "spill_bytes": 0,
                        "verdict": "ok", "notes": notes, "vram_free_bytes": vram}
            frac = 1.0 if ngl == -1 else min(1.0, ngl / n_layer)
            on_gpu = min(total, gpu_room) if ngl == -1 else total * frac
            spill = max(0, int(total - on_gpu))
            notes.append(f"About {spill / 1e9:.1f} GB will run from system RAM on the CPU, which is slower")
            have = vram + ram
            v = verdict(total, have)
            if ngl != -1 and on_gpu > vram:
                v = "over"
            return {"placement": "split", "limit": "vram+ram", "available_bytes": have, "spill_bytes": spill,
                    "verdict": v, "notes": notes, "vram_free_bytes": vram}
        v = verdict(total, vram)
        if v != "ok":
            notes.append("Set GPU layers to auto so llama.cpp can run what doesn't fit on the CPU")
        return {"placement": "gpu", "limit": "vram", "available_bytes": vram, "spill_bytes": 0,
                "verdict": v, "notes": notes, "vram_free_bytes": vram}

    def fit_on_gpu(self, entry: ModelEntry, preset: Preset) -> dict:
        """The largest context (f16 or q8_0 KV cache) that keeps every layer on the GPU."""
        from dataclasses import replace

        base = replace(preset, n_gpu_layers=-1)
        trained = entry.meta.get("context_length") or preset.ctx_per_request
        steps = sorted({c for c in (preset.ctx_per_request, 1048576, 524288, 262144, 131072, 65536, 32768, 16384, 8192, 4096, 2048)
                        if c <= max(preset.ctx_per_request, 2048) and c <= max(trained, 2048)}, reverse=True)
        for ctx in steps:
            for cache in ("f16", "q8_0"):
                fa = base.flash_attn if cache == "f16" or base.flash_attn != "off" else "auto"
                candidate = replace(base, ctx_per_request=ctx, cache_type_k=cache, cache_type_v=cache, flash_attn=fa)
                est = self.estimate(entry, candidate)
                if est["placement"] == "gpu" and est["verdict"] == "ok":
                    return {"fits": True, "preset": candidate.to_dict(), "estimate": est,
                            "message": f"Fits on the GPU with {ctx:,} tokens of context and a {cache} KV cache."}
        weights = entry.size_bytes
        return {"fits": False, "preset": preset.to_dict(), "estimate": self.estimate(entry, preset),
                "message": (f"The model's weights alone need {weights / 1e9:.1f} GB, more than this GPU can hold "
                            "with room to run. Pick a smaller quant to run it entirely on the GPU.")}

    def estimate(self, entry: ModelEntry, preset: Preset) -> dict:
        notes = []
        weights = entry.size_bytes
        kv = self.kv_bytes(entry.meta, preset)
        if not kv:
            notes.append("model metadata incomplete: KV cache not included")
        if entry.meta.get("ssm_state_size") and hybrid_attention_layers(entry.meta) is None:
            notes.append("hybrid/recurrent model: KV cache likely overestimated")
        compute = int(0.5 * GiB + 0.02 * weights)
        total = weights + kv + compute
        placed = self.place(total, preset, entry.meta.get("n_layer"))
        return {
            "estimate_bytes": total,
            "weights_bytes": weights,
            "kv_bytes": kv,
            "compute_bytes": compute,
            "available_bytes": placed["available_bytes"],
            "limit": placed["limit"],
            "placement": placed["placement"],
            "spill_bytes": placed["spill_bytes"],
            "vram_free_bytes": placed["vram_free_bytes"],
            "ram_available_bytes": self.probe.available_ram(),
            "verdict": placed["verdict"],
            "notes": notes + placed["notes"],
        }
