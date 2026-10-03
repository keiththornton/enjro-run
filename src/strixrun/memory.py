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
                             timeout=2, check=False).stdout.strip()
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

    def nvidia_free_bytes(self) -> int | None:
        exe = shutil.which("nvidia-smi")
        if not exe:
            return None
        try:
            out = subprocess.run([exe, "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=5, check=False).stdout
            free = [int(line.strip()) for line in out.splitlines() if line.strip()]
            return free[0] * 1024 * 1024 if free else None
        except (OSError, ValueError, subprocess.SubprocessError):
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
        n_ctx = preset.ctx_per_request * preset.parallel
        window, pattern = meta.get("sliding_window"), meta.get("sliding_window_pattern")
        if window and isinstance(pattern, int) and pattern > 1:
            n_global = n_layer // pattern
            n_swa = n_layer - n_global
            swa_ctx = min(n_ctx, window * preset.parallel)
            return int(per_pos_layer * (n_global * n_ctx + n_swa * swa_ctx))
        return int(per_pos_layer * n_layer * n_ctx)

    def available(self, preset: Preset) -> tuple[int, str]:
        ram = self.probe.available_ram()
        if preset.n_gpu_layers > 0:
            vram = self.probe.nvidia_free_bytes()
            if vram is not None:
                return vram, "vram"
            if self.probe.is_apple_silicon():
                limit_mb = self.probe.metal_wired_limit_mb()
                limit = limit_mb * 1024 * 1024 if limit_mb else int(METAL_DEFAULT_FRACTION * self.probe.total_ram())
                return min(ram, limit), "metal"
        return ram, "ram"

    def estimate(self, entry: ModelEntry, preset: Preset) -> dict:
        notes = []
        weights = entry.size_bytes
        kv = self.kv_bytes(entry.meta, preset)
        if not kv:
            notes.append("model metadata incomplete: KV cache not included")
        if entry.meta.get("ssm_state_size"):
            notes.append("hybrid/recurrent model: KV cache likely overestimated")
        compute = int(0.5 * GiB + 0.02 * weights)
        total = weights + kv + compute
        available, limit = self.available(preset)
        if total <= TIGHT_FRACTION * available:
            verdict = "ok"
        elif total <= available:
            verdict = "tight"
        else:
            verdict = "over"
        return {
            "estimate_bytes": total,
            "weights_bytes": weights,
            "kv_bytes": kv,
            "compute_bytes": compute,
            "available_bytes": available,
            "limit": limit,
            "verdict": verdict,
            "notes": notes,
        }

