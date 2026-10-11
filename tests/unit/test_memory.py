from dataclasses import dataclass

import pytest

from enjrorun.memory import CACHE_BYTES, MemoryEstimator
from enjrorun.presets import Preset
from enjrorun.registry import ModelEntry

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
    one = e.estimate(entry(QWEN25_15B), Preset(ctx_per_request=8192, parallel=1, kv_unified=False))["kv_bytes"]
    two = e.estimate(entry(QWEN25_15B), Preset(ctx_per_request=8192, parallel=2, kv_unified=False))["kv_bytes"]
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

    from enjrorun import memory

    monkeypatch.setattr(memory.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(memory.psutil, "virtual_memory",
                        lambda: SimpleNamespace(total=16 * GiB, available=3 * GiB))
    monkeypatch.setattr(memory, "_sysctl_int", lambda name: 47 if name == "kern.memorystatus_level" else None)
    assert memory.SystemProbe().available_ram() == 16 * GiB * 47 // 100


def test_non_macos_available_is_psutil(monkeypatch):
    from types import SimpleNamespace

    from enjrorun import memory

    monkeypatch.setattr(memory.platform, "system", lambda: "Linux")
    monkeypatch.setattr(memory.psutil, "virtual_memory",
                        lambda: SimpleNamespace(total=16 * GiB, available=3 * GiB))
    assert memory.SystemProbe().available_ram() == 3 * GiB


QWEN35_9B = {"n_layer": 32, "n_head_kv": 4, "head_dim_k": 256, "head_dim_v": 256, "context_length": 262144,
             "ssm_state_size": 128, "ssm_inner_size": 4096, "full_attention_interval": 4}


def test_hybrid_full_attention_interval_counts_only_attention_layers():
    e = MemoryEstimator(FakeProbe())
    est = e.estimate(entry(QWEN35_9B, size=int(4.94e9)), Preset(ctx_per_request=8192, parallel=2, kv_unified=False))
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


# ---- NVIDIA hosts: llama.cpp fits layers into VRAM and runs the rest from RAM ------------

RTX3090 = {"nvidia_free": int(23.5 * GiB), "available": 100 * GiB, "total": 128 * GiB}


def test_3090_with_128gb_fits_a_7gb_model_on_the_gpu():
    est = MemoryEstimator(FakeProbe(**RTX3090)).estimate(entry(LLAMA3_8B, size=7 * GiB), Preset())
    assert est["verdict"] == "ok" and est["placement"] == "gpu" and est["limit"] == "vram"
    assert est["spill_bytes"] == 0


def test_too_big_for_vram_but_fits_with_ram_is_ok_split():
    est = MemoryEstimator(FakeProbe(**RTX3090)).estimate(entry(LLAMA3_8B, size=40 * GiB), Preset())
    assert est["verdict"] == "ok" and est["placement"] == "split"
    assert est["spill_bytes"] > 15 * GiB
    assert est["available_bytes"] == int(23.5 * GiB) + 100 * GiB
    assert any("CPU" in n for n in est["notes"])


def test_too_big_for_vram_and_ram_is_over():
    est = MemoryEstimator(FakeProbe(**RTX3090)).estimate(entry(LLAMA3_8B, size=150 * GiB), Preset())
    assert est["verdict"] == "over"


def test_cpu_only_layers_compare_with_ram():
    est = MemoryEstimator(FakeProbe(**RTX3090)).estimate(entry(LLAMA3_8B, size=7 * GiB), Preset(n_gpu_layers=0))
    assert est["placement"] == "cpu" and est["limit"] == "ram" and est["verdict"] == "ok"


def test_explicit_all_layers_must_fit_vram():
    est = MemoryEstimator(FakeProbe(**RTX3090)).estimate(entry(LLAMA3_8B, size=40 * GiB), Preset(n_gpu_layers=99))
    assert est["placement"] == "gpu" and est["verdict"] == "over"
    assert any("auto" in n.lower() for n in est["notes"])


def test_low_free_vram_is_explained():
    probe = FakeProbe(nvidia_free=2 * GiB, available=100 * GiB, total=128 * GiB)
    est = MemoryEstimator(probe).estimate(entry(LLAMA3_8B, size=7 * GiB), Preset())
    assert est["placement"] == "split" and est["verdict"] == "ok"
    assert est["vram_free_bytes"] == 2 * GiB


def test_nvidia_smi_lists_each_gpu_with_its_index(monkeypatch):
    from types import SimpleNamespace

    from enjrorun import memory

    monkeypatch.setattr(memory.shutil, "which", lambda name: "nvidia-smi")
    out = "0, NVIDIA GeForce RTX 3060, 12288, 11000\n1, NVIDIA GeForce RTX 3090, 24576, 23000\n"
    monkeypatch.setattr(memory.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=out))
    probe = memory.SystemProbe()
    gpus = probe.gpus()
    assert [(g["index"], g["name"]) for g in gpus] == [(0, "NVIDIA GeForce RTX 3060"), (1, "NVIDIA GeForce RTX 3090")]
    assert gpus[1]["total_bytes"] == 24576 * 1024 * 1024
    assert probe.nvidia_free_bytes() == (23000 + 11000) * 1024 * 1024


# ---- several NVIDIA GPUs: the preset picks which ones ------------------------------------

@dataclass
class TwoGpuProbe(FakeProbe):
    available: int = 100 * GiB
    total: int = 128 * GiB

    def gpus(self):
        return [{"index": 0, "name": "RTX 3060", "total_bytes": 12 * GiB, "free_bytes": 11 * GiB},
                {"index": 1, "name": "RTX 3090", "total_bytes": 24 * GiB, "free_bytes": 23 * GiB}]


def test_all_gpus_split_adds_vram_less_per_gpu_buffers():
    from enjrorun.memory import PER_EXTRA_GPU

    est = MemoryEstimator(TwoGpuProbe()).estimate(entry(LLAMA3_8B, size=20 * GiB), Preset())
    assert est["available_bytes"] == 34 * GiB - PER_EXTRA_GPU
    assert [g["index"] for g in est["gpus"]] == [0, 1]
    assert est["placement"] == "gpu"
    assert any("Split across 2 GPUs" in n for n in est["notes"])


def test_one_chosen_gpu_counts_only_its_vram():
    e = MemoryEstimator(TwoGpuProbe())
    big = e.estimate(entry(LLAMA3_8B, size=16 * GiB), Preset(gpus=[1]))
    assert big["available_bytes"] == 23 * GiB and big["placement"] == "gpu" and big["verdict"] == "ok"
    small = e.estimate(entry(LLAMA3_8B, size=16 * GiB), Preset(gpus=[0]))
    assert small["vram_free_bytes"] == 11 * GiB and small["placement"] == "split"
    assert [g["name"] for g in small["gpus"]] == ["RTX 3060"]


def test_missing_gpu_is_explained():
    est = MemoryEstimator(TwoGpuProbe()).estimate(entry(LLAMA3_8B), Preset(gpus=[0, 3]))
    assert est["vram_free_bytes"] == 11 * GiB
    assert any("GPU 3 isn't on this machine" in n for n in est["notes"])
    none = MemoryEstimator(TwoGpuProbe()).estimate(entry(LLAMA3_8B), Preset(gpus=[3]))
    assert none["placement"] == "cpu" and any("on the CPU" in n for n in none["notes"])


def test_fit_on_gpu_uses_the_chosen_gpu():
    e = MemoryEstimator(TwoGpuProbe())
    meta = {**LLAMA3_8B, "context_length": 131072}
    small = e.fit_on_gpu(entry(meta, size=7 * GiB), Preset(gpus=[0], ctx_per_request=131072))
    big = e.fit_on_gpu(entry(meta, size=7 * GiB), Preset(gpus=[1], ctx_per_request=131072))
    assert small["fits"] and big["fits"]
    assert big["preset"]["ctx_per_request"] > small["preset"]["ctx_per_request"]
    assert big["preset"]["gpus"] == [1]


def test_nvidia_unparseable_output_means_no_gpu(monkeypatch):
    from types import SimpleNamespace

    from enjrorun import memory

    monkeypatch.setattr(memory.shutil, "which", lambda name: "nvidia-smi")
    monkeypatch.setattr(memory.subprocess, "run",
                        lambda *a, **kw: SimpleNamespace(stdout="NVIDIA-SMI has failed because it couldn't communicate"))
    assert memory.SystemProbe().nvidia_free_bytes() is None


def test_unified_kv_counts_one_pool():
    e = MemoryEstimator(FakeProbe())
    unified = e.estimate(entry(LLAMA3_8B), Preset(ctx_per_request=8192, parallel=2))["kv_bytes"]
    split = e.estimate(entry(LLAMA3_8B), Preset(ctx_per_request=8192, parallel=2, kv_unified=False))["kv_bytes"]
    assert unified == 1 * GiB and split == 2 * GiB


def test_fit_on_gpu_suggests_largest_context_that_keeps_every_layer_on_the_gpu():
    probe = FakeProbe(nvidia_free=24 * GiB, available=100 * GiB, total=128 * GiB)
    meta = {**LLAMA3_8B, "context_length": 131072}
    s = MemoryEstimator(probe).fit_on_gpu(entry(meta, size=16 * GiB), Preset(ctx_per_request=65536))
    assert s["fits"] and s["estimate"]["placement"] == "gpu" and s["estimate"]["verdict"] == "ok"
    assert s["preset"]["n_gpu_layers"] == -1
    assert 2048 <= s["preset"]["ctx_per_request"] < 65536
    # q8_0 halves the KV cache, so it is chosen when it buys more context
    assert s["preset"]["cache_type_k"] in ("f16", "q8_0")


def test_fit_on_gpu_tries_contexts_above_128k():
    probe = FakeProbe(nvidia_free=200 * GiB, available=100 * GiB, total=256 * GiB)
    meta = {**LLAMA3_8B, "context_length": 1048576}
    s = MemoryEstimator(probe).fit_on_gpu(entry(meta, size=5 * GiB), Preset(ctx_per_request=1048576))
    assert s["fits"] and 131072 < s["preset"]["ctx_per_request"] <= 1048576


def test_fit_on_gpu_keeps_current_settings_when_they_already_fit():
    probe = FakeProbe(nvidia_free=24 * GiB, available=100 * GiB, total=128 * GiB)
    s = MemoryEstimator(probe).fit_on_gpu(entry(LLAMA3_8B, size=5 * GiB), Preset(ctx_per_request=8192))
    assert s["fits"] and s["preset"]["ctx_per_request"] == 8192 and s["preset"]["cache_type_k"] == "f16"


def test_fit_on_gpu_explains_when_weights_alone_are_too_big():
    probe = FakeProbe(nvidia_free=24 * GiB, available=100 * GiB, total=128 * GiB)
    s = MemoryEstimator(probe).fit_on_gpu(entry(LLAMA3_8B, size=40 * GiB), Preset())
    assert not s["fits"] and "smaller quant" in s["message"]
