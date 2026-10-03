import re

import pytest

from strixrun.presets import Preset, PresetError, PresetStore, default_preset, llama_args


def test_defaults_cap_context_and_detect_embedding():
    p = default_preset({"context_length": 32768})
    assert p.ctx_per_request == 8192 and p.parallel == 2 and p.mode == "chat"
    assert default_preset({"context_length": 4096}).ctx_per_request == 4096
    assert default_preset({}).ctx_per_request == 8192
    assert default_preset({"embedding_only": True}).mode == "embedding"


def test_llama_args_multiplies_context_by_parallel(tmp_path):
    args = llama_args(Preset(ctx_per_request=8192, parallel=2), tmp_path)
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
