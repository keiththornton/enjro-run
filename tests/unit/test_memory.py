from dataclasses import dataclass

import pytest

from strixrun.memory import CACHE_BYTES, MemoryEstimator
from strixrun.presets import Preset
from strixrun.registry import ModelEntry

GiB = 1024**3

LLAMA3_8B = {"n_layer": 32, "n_head": 32, "n_head_kv": 8, "head_dim_k": 128, "head_dim_v": 128,
             "context_length": 8192}
QWEN25_15B = {"n_layer": 28, "n_head": 12, "n_head_kv": 2, "head_dim_k": 128, "head_dim_v": 128,
              "context_length": 32768}


@dataclass
class FakeProbe:
    total: int = 16 * GiB
    available: int = 12 * GiB
    apple_silicon: bool = False
    wired_limit_mb: int | None = None
    nvidia_free: int | None = None

    def total_ram(self):
        return self.total

    def available_ram(self):
        return self.available

    def is_apple_silicon(self):
        return self.apple_silicon

    def metal_wired_limit_mb(self):
        return self.wired_limit_mb

    def nvidia_free_bytes(self):
        return self.nvidia_free


def entry(meta, size=4 * GiB):
    return ModelEntry(id="m", aliases=["m"], paths=["/m.gguf"], size_bytes=size, meta=meta)


def test_llama3_8b_kv_is_one_gib_at_8k_f16():
    est = MemoryEstimator(FakeProbe()).estimate(entry(LLAMA3_8B), Preset(ctx_per_request=8192, parallel=1))
    assert est["kv_bytes"] == 1 * GiB


def test_qwen_kv_scales_with_parallel():
    e = MemoryEstimator(FakeProbe())
    one = e.estimate(entry(QWEN25_15B), Preset(ctx_per_request=8192, parallel=1))["kv_bytes"]
    two = e.estimate(entry(QWEN25_15B), Preset(ctx_per_request=8192, parallel=2))["kv_bytes"]
    assert one == 28 * 8192 * 2 * (128 * 2 + 128 * 2) == 234_881_024
    assert two == 2 * one


def test_quantized_cache_shrinks_kv():
    e = MemoryEstimator(FakeProbe())
    f16 = e.estimate(entry(LLAMA3_8B), Preset(parallel=1))["kv_bytes"]
    q8 = e.estimate(entry(LLAMA3_8B), Preset(parallel=1, cache_type_k="q8_0", cache_type_v="q8_0"))["kv_bytes"]
    assert q8 == pytest.approx(f16 * CACHE_BYTES["q8_0"] / 2, rel=1e-9)


def test_total_is_weights_kv_compute():
    est = MemoryEstimator(FakeProbe()).estimate(entry(LLAMA3_8B, size=5 * GiB), Preset(parallel=1))
    assert est["weights_bytes"] == 5 * GiB
    assert est["compute_bytes"] == int(0.5 * GiB + 0.02 * 5 * GiB)
    assert est["estimate_bytes"] == est["weights_bytes"] + est["kv_bytes"] + est["compute_bytes"]


def test_sliding_window_layers_only_cache_the_window():
    meta = {**LLAMA3_8B, "sliding_window": 1024, "sliding_window_pattern": 4}
    e = MemoryEstimator(FakeProbe())
    full = e.estimate(entry(LLAMA3_8B), Preset(ctx_per_request=8192, parallel=1))["kv_bytes"]
    swa = e.estimate(entry(meta), Preset(ctx_per_request=8192, parallel=1))["kv_bytes"]
    # 8 global layers keep 8192 positions, 24 sliding layers keep 1024
    per_pos_layer = 8 * 512
    assert swa == 8 * 8192 * per_pos_layer + 24 * 1024 * per_pos_layer
    assert swa < full


def test_missing_metadata_still_estimates_weights():
    est = MemoryEstimator(FakeProbe()).estimate(entry({}, size=2 * GiB), Preset())
    assert est["kv_bytes"] == 0 and "metadata" in " ".join(est["notes"])


def test_metal_limit_caps_available_on_16gb_mac():
    probe = FakeProbe(apple_silicon=True, total=16 * GiB, available=14 * GiB)
    est = MemoryEstimator(probe).estimate(entry(LLAMA3_8B), Preset(parallel=1))
    assert est["available_bytes"] <= int(0.67 * 16 * GiB)
    assert est["limit"] == "metal"


def test_metal_wired_limit_sysctl_wins():
    probe = FakeProbe(apple_silicon=True, available=14 * GiB, wired_limit_mb=13000)
    est = MemoryEstimator(probe).estimate(entry(LLAMA3_8B), Preset(parallel=1))
    assert est["available_bytes"] == 13000 * 1024 * 1024


def test_cpu_only_ignores_gpu_limits():
    probe = FakeProbe(apple_silicon=True, available=14 * GiB, nvidia_free=1 * GiB)
    est = MemoryEstimator(probe).estimate(entry(LLAMA3_8B), Preset(parallel=1, n_gpu_layers=0))
    assert est["available_bytes"] == 14 * GiB and est["limit"] == "ram"


def test_nvidia_uses_free_vram():
    probe = FakeProbe(nvidia_free=10 * GiB, available=30 * GiB)
    est = MemoryEstimator(probe).estimate(entry(LLAMA3_8B), Preset(parallel=1))
    assert est["available_bytes"] == 10 * GiB and est["limit"] == "vram"


@pytest.mark.parametrize("size,verdict", [(3 * GiB, "ok"), (8 * GiB, "tight"), (12 * GiB, "over")])
def test_verdicts(size, verdict):
    probe = FakeProbe(available=10 * GiB)  # kv 1 GiB + compute ~0.5 GiB + weights
    est = MemoryEstimator(probe).estimate(entry(LLAMA3_8B, size=size), Preset(ctx_per_request=8192, parallel=1))
    assert est["verdict"] == verdict


def test_macos_available_uses_memorystatus_level(monkeypatch):
    from types import SimpleNamespace

    from strixrun import memory

    monkeypatch.setattr(memory.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(memory.psutil, "virtual_memory",
                        lambda: SimpleNamespace(total=16 * GiB, available=3 * GiB))
    monkeypatch.setattr(memory, "_sysctl_int", lambda name: 47 if name == "kern.memorystatus_level" else None)
    assert memory.SystemProbe().available_ram() == 16 * GiB * 47 // 100


def test_non_macos_available_is_psutil(monkeypatch):
    from types import SimpleNamespace

    from strixrun import memory

    monkeypatch.setattr(memory.platform, "system", lambda: "Linux")
    monkeypatch.setattr(memory.psutil, "virtual_memory",
                        lambda: SimpleNamespace(total=16 * GiB, available=3 * GiB))
    assert memory.SystemProbe().available_ram() == 3 * GiB


QWEN35_9B = {"n_layer": 32, "n_head_kv": 4, "head_dim_k": 256, "head_dim_v": 256, "context_length": 262144,
             "ssm_state_size": 128, "ssm_inner_size": 4096, "full_attention_interval": 4}


def test_hybrid_full_attention_interval_counts_only_attention_layers():
    e = MemoryEstimator(FakeProbe())
    est = e.estimate(entry(QWEN35_9B, size=int(4.94e9)), Preset(ctx_per_request=8192, parallel=2))
    attn_kv = 8 * 16384 * 4 * (256 * 2 + 256 * 2)  # 8 of 32 layers keep a KV cache
    recurrent = 24 * 2 * 4096 * 128 * 4            # small fixed state per recurrent layer and sequence
    assert est["kv_bytes"] == attn_kv + recurrent
    assert est["kv_bytes"] < 0.7e9
    assert not any("overestimated" in n for n in est["notes"])


def test_hybrid_per_layer_kv_heads_list():
    meta = {"n_layer": 4, "n_head_kv": 8, "n_attn_layers": 1, "head_dim_k": 128, "head_dim_v": 128,
            "ssm_state_size": 16, "ssm_inner_size": 1024}
    est = MemoryEstimator(FakeProbe()).estimate(entry(meta), Preset(ctx_per_request=1024, parallel=1))
    assert est["kv_bytes"] == 1 * 1024 * 8 * (128 * 2 + 128 * 2) + 3 * 1 * 1024 * 16 * 4


def test_hybrid_without_layout_still_flags_overestimate():
    meta = {**LLAMA3_8B, "ssm_state_size": 128}
    est = MemoryEstimator(FakeProbe()).estimate(entry(meta), Preset(parallel=1))
    assert any("overestimated" in n for n in est["notes"])
