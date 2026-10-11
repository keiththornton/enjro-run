import re

import pytest

from enjrorun.registry import (
    ModelEntry,
    Registry,
    RegistryError,
    default_alias,
    group_shards,
    make_id,
)
from tests.fixtures.make_tiny_gguf import write_gguf

SAFE = re.compile(r"^[a-z0-9._-]+$")


def entry(owner="bartowski", repo="Qwen2.5-7B-Instruct-GGUF", quant="Q4_K_M", **kw):
    return ModelEntry.from_download(owner, repo, quant, paths=[f"/m/{repo}.gguf"], size_bytes=10, **kw)


def test_make_id_is_filename_safe_and_stable():
    mid = make_id("bartowski", "Qwen2.5-7B-Instruct-GGUF", "Q4_K_M")
    assert mid == "bartowski--qwen2.5-7b-instruct--q4_k_m"
    assert SAFE.match(make_id("Some Owner", "weird:repo/name GGUF", "IQ4_XS"))
    assert ":" not in make_id("a", "b:c", "d")


def test_default_alias_short_form():
    assert default_alias("Qwen2.5-7B-Instruct-GGUF", "Q4_K_M", "bartowski", set()) == (
        "qwen2.5-7b-instruct:q4_k_m"
    )


def test_default_alias_collision_gets_owner_suffix():
    taken = {"qwen2.5-7b-instruct:q4_k_m"}
    assert default_alias("Qwen2.5-7B-Instruct-GGUF", "Q4_K_M", "unsloth", taken) == (
        "qwen2.5-7b-instruct:q4_k_m@unsloth"
    )


def test_two_owners_get_distinct_ids_and_aliases(tmp_path):
    reg = Registry(tmp_path / "models.json")
    a = reg.add(entry(owner="bartowski"))
    b = reg.add(entry(owner="unsloth"))
    assert a.id != b.id
    assert a.aliases[0] != b.aliases[0]
    assert reg.resolve(a.aliases[0]).id == a.id
    assert reg.resolve(b.aliases[0]).id == b.id


def test_resolve_by_id_and_unknown(tmp_path):
    reg = Registry(tmp_path / "models.json")
    a = reg.add(entry())
    assert reg.resolve(a.id).id == a.id
    assert reg.resolve("nope") is None


def test_persistence_roundtrip(tmp_path):
    p = tmp_path / "models.json"
    a = Registry(p).add(entry())
    again = Registry(p)
    assert again.get(a.id).aliases == a.aliases
    assert again.get(a.id).location == "store"


def test_set_aliases_validates_uniqueness(tmp_path):
    reg = Registry(tmp_path / "models.json")
    a = reg.add(entry(owner="bartowski"))
    b = reg.add(entry(owner="unsloth"))
    reg.set_aliases(a.id, ["gpt-4o-mini", "qwen-small"])
    assert reg.resolve("gpt-4o-mini").id == a.id
    with pytest.raises(RegistryError, match="already used"):
        reg.set_aliases(b.id, ["gpt-4o-mini"])
    with pytest.raises(RegistryError, match="already used"):
        reg.set_aliases(b.id, [a.id])
    with pytest.raises(RegistryError, match="invalid alias"):
        reg.set_aliases(b.id, ["has space"])
    with pytest.raises(RegistryError, match="at least one"):
        reg.set_aliases(b.id, [])


def test_remove(tmp_path):
    reg = Registry(tmp_path / "models.json")
    a = reg.add(entry())
    reg.remove(a.id)
    assert reg.get(a.id) is None
    assert reg.resolve(a.aliases[0]) is None


def test_group_shards():
    files = [
        "/d/big-00002-of-00003.gguf",
        "/d/big-00001-of-00003.gguf",
        "/d/big-00003-of-00003.gguf",
        "/d/small.gguf",
    ]
    groups = group_shards(files)
    assert groups["/d/big-00001-of-00003.gguf"] == [
        "/d/big-00001-of-00003.gguf",
        "/d/big-00002-of-00003.gguf",
        "/d/big-00003-of-00003.gguf",
    ]
    assert groups["/d/small.gguf"] == ["/d/small.gguf"]


def test_group_shards_incomplete_set_is_skipped():
    groups = group_shards(["/d/big-00001-of-00003.gguf", "/d/big-00002-of-00003.gguf"])
    assert groups == {}


def test_scan_registers_scan_models_once_with_metadata(tmp_path):
    d = tmp_path / "lm"
    d.mkdir()
    write_gguf(d / "My-Model.Q8_0.gguf")
    (d / "notes.txt").write_text("ignore")
    (d / "broken.Q4_K_M.gguf").write_bytes(b"not a gguf")
    reg = Registry(tmp_path / "models.json")
    added = reg.scan([d], location="scan")
    assert len(added) == 1
    m = added[0]
    assert m.location == "scan" and SAFE.match(m.id)
    assert m.meta["architecture"] == "llama" and m.meta["n_layer"] == 2
    assert reg.scan([d], location="scan") == []  # already known


def test_scan_of_store_layout_matches_download_id(tmp_path):
    d = tmp_path / "store" / "nomic-ai--nomic-embed-text-v1.5-GGUF"
    d.mkdir(parents=True)
    write_gguf(d / "nomic-embed-text-v1.5.Q4_K_M.gguf", arch="nomic-bert", chat_template=None)
    reg = Registry(tmp_path / "models.json")
    (m,) = reg.scan([tmp_path / "store"], location="store")
    assert m.id == make_id("nomic-ai", "nomic-embed-text-v1.5-GGUF", "Q4_K_M")
    assert m.repo == "nomic-ai/nomic-embed-text-v1.5-GGUF" and m.quant == "Q4_K_M"
    assert m.meta["embedding_only"] is True


def test_scan_ignores_mmproj_and_incomplete(tmp_path):
    d = tmp_path / "lm"
    d.mkdir()
    (d / "mmproj-model-f16.gguf").write_bytes(b"x")
    (d / "partial.gguf.incomplete").write_bytes(b"x")
    assert Registry(tmp_path / "models.json").scan([d], location="scan") == []


def test_refresh_stale_meta(tmp_path):
    f = write_gguf(tmp_path / "m.gguf")
    reg = Registry(tmp_path / "models.json")
    old = reg.add(ModelEntry(id="x--m--q4", aliases=["m"], paths=[str(f)], size_bytes=1,
                             meta={"architecture": "llama"}))  # parsed by an older Enjro Run
    gone = reg.add(ModelEntry(id="x--gone--q4", aliases=["g"], paths=[str(tmp_path / "missing.gguf")],
                              size_bytes=1, meta={}))
    assert reg.refresh_stale_meta() == [old.id]
    assert "n_attn_layers" in Registry(tmp_path / "models.json").get(old.id).meta
    assert reg.get(gone.id).meta == {}
    assert reg.refresh_stale_meta() == []  # already current
