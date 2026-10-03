"""Generate small GGUF fixtures with gguf.GGUFWriter.

Usage: python tests/fixtures/make_tiny_gguf.py [out_dir]   (default: tests/fixtures)
Tests also call `write_gguf` directly to build variants in tmp dirs.
"""

import sys
from pathlib import Path

import gguf
import numpy as np


def write_gguf(path: Path, arch: str = "llama", *, context_length: int = 4096, block_count: int = 2,
               embedding_length: int = 64, head_count: int = 4, head_count_kv: int | list[int] = 2,
               key_length: int | None = None, sliding_window: int | None = None,
               pooling_type: int | None = None, chat_template: str | None = "{{ messages }}",
               n_tokens: int = 1000, split: tuple[int, int] | None = None) -> Path:
    w = gguf.GGUFWriter(str(path), arch)
    w.add_name("Tiny Test Model")
    w.add_size_label("1M")
    w.add_file_type(15)  # MOSTLY_Q4_K_M
    w.add_context_length(context_length)
    w.add_block_count(block_count)
    w.add_embedding_length(embedding_length)
    w.add_head_count(head_count)
    if isinstance(head_count_kv, list):
        w.add_array(f"{arch}.attention.head_count_kv", head_count_kv)
    else:
        w.add_head_count_kv(head_count_kv)
    if key_length:
        w.add_key_length(key_length)
        w.add_value_length(key_length)
    if sliding_window:
        w.add_sliding_window(sliding_window)
    if pooling_type is not None:
        w.add_pooling_type(gguf.PoolingType(pooling_type))
    if split:
        w.add_uint16("split.no", split[0])
        w.add_uint16("split.count", split[1])
    w.add_tokenizer_model("gpt2")
    w.add_token_list([f"token_{i}" for i in range(n_tokens)])
    if chat_template:
        w.add_chat_template(chat_template)
    w.add_tensor("token_embd.weight", np.zeros((4, 8), dtype=np.float32))
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file()
    w.close()
    return path


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
    print(write_gguf(out / "tiny.gguf"))
