import re

import pytest

from strixrun.presets import Preset, PresetError, PresetStore, default_preset, gpu_env, llama_args


def test_defaults_cap_context_and_detect_embedding():
    p = default_preset({"context_length": 32768})
    assert p.ctx_per_request == 8192 and p.parallel == 2 and p.mode == "chat"
    assert default_preset({"context_length": 4096}).ctx_per_request == 4096
    assert default_preset({}).ctx_per_request == 8192
    assert default_preset({"embedding_only": True}).mode == "embedding"


def test_llama_args_multiplies_context_by_parallel(tmp_path):
    args = llama_args(Preset(ctx_per_request=8192, parallel=2, kv_unified=False), tmp_path)
    assert args[args.index("-c") + 1] == "16384"
    assert args[args.index("--parallel") + 1] == "2"
    assert "--jinja" in args
    assert "--embeddings" not in args


def test_llama_args_embedding_mode(tmp_path):
    assert "--embeddings" in llama_args(Preset(mode="embedding"), tmp_path)


def test_llama_args_cache_and_flash_attn(tmp_path):
    args = llama_args(Preset(flash_attn="on", cache_type_k="q8_0", cache_type_v="q8_0"), tmp_path)
    assert args[args.index("--flash-attn") + 1] == "on"
    assert args[args.index("-ctk") + 1] == "q8_0"


def test_quantized_v_cache_requires_flash_attn():
    with pytest.raises(PresetError, match="flash"):
        Preset(flash_attn="off", cache_type_v="q8_0").validate()


@pytest.mark.parametrize(
    "extra",
    [
        ["--threads", "8"],
        ["-t", "8", "--no-mmap", "--mlock"],
        ["--batch-size", "1024", "--ubatch-size", "512"],
        ["--kv-unified"],
    ],
)
def test_allowed_extra_args(extra, tmp_path):
    p = Preset(extra_args=extra)
    p.validate()
    assert llama_args(p, tmp_path)[-len(extra):] == extra


@pytest.mark.parametrize(
    "extra",
    [
        ["--log-file", "/tmp/x"],
        ["--slot-save-path", "/tmp"],
        ["--lora", "a.gguf"],
        ["-m", "/etc/passwd"],
        ["--model", "x"],
        ["--threads", "../../x"],
        ["--host", "0.0.0.0"],
        ["--api-key", "x"],
        ["--threads", "4;rm"],
    ],
)
def test_rejected_extra_args(extra):
    with pytest.raises(PresetError):
        Preset(extra_args=extra).validate()


def test_template_containment(tmp_path):
    (tmp_path / "chatml.jinja").write_text("{{x}}")
    args = llama_args(Preset(chat_template="chatml.jinja"), tmp_path)
    assert args[args.index("--chat-template-file") + 1] == str(tmp_path / "chatml.jinja")
    for bad in ["../x.jinja", "/etc/passwd", "sub/../../x", "C:\\x.jinja"]:
        with pytest.raises(PresetError):
            llama_args(Preset(chat_template=bad), tmp_path)
    with pytest.raises(PresetError, match="not found"):
        llama_args(Preset(chat_template="missing.jinja"), tmp_path)


def test_store_saves_by_id_with_safe_filename(tmp_path):
    store = PresetStore(tmp_path)
    mid = "bartowski--qwen2.5-7b-instruct--q4_k_m"
    store.save(mid, Preset(ctx_per_request=4096))
    files = list(tmp_path.iterdir())
    assert len(files) == 1 and re.match(r"^[a-z0-9._-]+\.json$", files[0].name)
    assert store.load(mid, {}).ctx_per_request == 4096


def test_store_falls_back_to_defaults(tmp_path):
    assert PresetStore(tmp_path).load("x--y--z", {"context_length": 2048}).ctx_per_request == 2048


def test_store_rejects_unsafe_id(tmp_path):
    with pytest.raises(PresetError):
        PresetStore(tmp_path).save("qwen:q4", Preset())
    with pytest.raises(PresetError):
        PresetStore(tmp_path).save("../evil", Preset())


def test_from_dict_ignores_unknown_and_validates():
    p = Preset.from_dict({"ctx_per_request": 1024, "junk": 1})
    assert p.ctx_per_request == 1024
    with pytest.raises(PresetError):
        Preset.from_dict({"parallel": 0})


def test_gpu_layers_default_auto_lets_llama_cpp_fit(tmp_path):
    p = Preset()
    assert p.n_gpu_layers == -1
    args = llama_args(p, tmp_path)
    assert "-ngl" not in args  # unset: llama.cpp's --fit chooses how many layers go to the GPU
    assert llama_args(Preset(n_gpu_layers=20), tmp_path)[llama_args(Preset(n_gpu_layers=20), tmp_path).index("-ngl") + 1] == "20"


def test_legacy_999_preset_becomes_auto():
    assert Preset.from_dict({"n_gpu_layers": 999}).n_gpu_layers == -1


def test_gpu_layers_validation():
    Preset(n_gpu_layers=-1).validate()
    Preset(n_gpu_layers=0).validate()
    with pytest.raises(PresetError):
        Preset(n_gpu_layers=-2).validate()


def test_unified_kv_by_default_uses_one_pool(tmp_path):
    args = llama_args(Preset(ctx_per_request=8192, parallel=2), tmp_path)
    assert args[args.index("-c") + 1] == "8192"  # shared by both slots, not 2 x 8192
    assert "--kv-unified" in args
    old = llama_args(Preset(ctx_per_request=8192, parallel=2, kv_unified=False), tmp_path)
    assert old[old.index("-c") + 1] == "16384" and "--no-kv-unified" in old


def test_llama_server_logs_layer_placement(tmp_path):
    args = llama_args(Preset(), tmp_path)
    assert args[args.index("-lv") + 1] == "4"  # shows 'offloaded X/Y layers to GPU'


def test_gpus_default_to_all_and_validate():
    assert Preset().gpus is None
    assert Preset.from_dict({"gpus": [1]}).gpus == [1]
    Preset(gpus=[0, 1]).validate()
    for bad in ([], [-1], [16], [0, 0], ["1"], [True], "1"):
        with pytest.raises(PresetError):
            Preset(gpus=bad).validate()


def test_gpu_env_orders_cuda_like_nvidia_smi_and_limits_devices():
    assert gpu_env(Preset()) == {"CUDA_DEVICE_ORDER": "PCI_BUS_ID"}
    assert gpu_env(Preset(gpus=[1])) == {"CUDA_DEVICE_ORDER": "PCI_BUS_ID", "CUDA_VISIBLE_DEVICES": "1"}
    assert gpu_env(Preset(gpus=[0, 1]))["CUDA_VISIBLE_DEVICES"] == "0,1"
    assert "CUDA_VISIBLE_DEVICES" not in gpu_env(Preset(gpus=[1], n_gpu_layers=0))  # CPU only
