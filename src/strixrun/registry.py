"""Inventory of local GGUF models (models.json).

Every model has an immutable, filename-safe `id`, used for files, admin routes and
log rows. `aliases` are what API clients put in the `model` field. Aliases are
unique across all models and can be edited.
"""

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from strixrun.gguf_meta import GGUFError, read_local_meta

SHARD_RE = re.compile(r"^(?P<stem>.+)-(?P<idx>\d{5})-of-(?P<total>\d{5})\.gguf$", re.IGNORECASE)
ALIAS_RE = re.compile(r"^[A-Za-z0-9._:@/+-]{1,128}$")
QUANT_RE = re.compile(
    r"(?<![A-Za-z0-9])(IQ\d_[A-Z]+|Q\d_K_[SMLX]L?|Q\d_K|Q\d_\d|Q\d|TQ\d_\d|MXFP4(?:_MOE)?|BF16|F16|F32)(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def quant_of(filename: str) -> str | None:
    matches = QUANT_RE.findall(Path(filename).name)
    return matches[-1].upper() if matches else None


class RegistryError(ValueError):
    pass


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-.")
    return re.sub(r"-{2,}", "-", s) or "model"


def _repo_base(repo: str) -> str:
    return re.sub(r"[-_.]?gguf$", "", repo, flags=re.IGNORECASE)


def make_id(owner: str, repo: str, quant: str) -> str:
    return f"{_slug(owner)}--{_slug(_repo_base(repo))}--{_slug(quant)}"


def default_alias(repo: str, quant: str, owner: str | None, taken: set[str]) -> str:
    base = f"{_slug(_repo_base(repo))}:{_slug(quant)}"
    if base not in taken:
        return base
    if owner:
        candidate = f"{base}@{_slug(owner)}"
        if candidate not in taken:
            return candidate
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def group_shards(files: list[str]) -> dict[str, list[str]]:
    """Map each loadable model's first file to all of its files. Incomplete shard sets are dropped."""
    singles: dict[str, list[str]] = {}
    sets: dict[tuple[str, str, int], list[tuple[int, str]]] = {}
    for f in files:
        m = SHARD_RE.match(os.path.basename(f))
        if m:
            key = (os.path.dirname(f), m["stem"], int(m["total"]))
            sets.setdefault(key, []).append((int(m["idx"]), f))
        else:
            singles[f] = [f]
    for (_, _, total), parts in sets.items():
        parts.sort()
        if [i for i, _ in parts] == list(range(1, total + 1)):
            singles[parts[0][1]] = [p for _, p in parts]
    return singles


@dataclass
class ModelEntry:
    id: str
    aliases: list[str]
    paths: list[str]
    size_bytes: int
    location: str = "store"  # "store" (StrixRun-managed) or "scan" (read-only folder)
    repo: str | None = None  # "owner/repo"
    quant: str | None = None
    meta: dict = field(default_factory=dict)
    added_at: float = field(default_factory=time.time)

    @classmethod
    def from_download(
        cls, owner: str, repo: str, quant: str, paths: list[str], size_bytes: int, meta: dict | None = None
    ) -> "ModelEntry":
        return cls(
            id=make_id(owner, repo, quant),
            aliases=[],
            paths=paths,
            size_bytes=size_bytes,
            location="store",
            repo=f"{owner}/{repo}",
            quant=quant,
            meta=meta or {},
        )

    @property
    def owner(self) -> str | None:
        return self.repo.split("/", 1)[0] if self.repo else None

    @property
    def primary_path(self) -> str:
        return self.paths[0]


class Registry:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._models: dict[str, ModelEntry] = {}
        if self.path.exists():
            for raw in json.loads(self.path.read_text()).get("models", []):
                e = ModelEntry(**raw)
                self._models[e.id] = e

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"models": [asdict(m) for m in self._models.values()]}, indent=2))
        os.replace(tmp, self.path)

    def _taken(self, exclude: str | None = None) -> set[str]:
        names: set[str] = set()
        for m in self._models.values():
            if m.id != exclude:
                names.add(m.id)
                names.update(m.aliases)
        return names

    def all(self) -> list[ModelEntry]:
        return list(self._models.values())

    def get(self, model_id: str) -> ModelEntry | None:
        return self._models.get(model_id)

    def resolve(self, name: str) -> ModelEntry | None:
        if name in self._models:
            return self._models[name]
        for m in self._models.values():
            if name in m.aliases:
                return m
        return None

    def add(self, entry: ModelEntry) -> ModelEntry:
        if entry.id in self._models:
            n = 2
            while f"{entry.id}-{n}" in self._models:
                n += 1
            entry.id = f"{entry.id}-{n}"
        if not entry.aliases:
            repo_name = entry.repo.split("/", 1)[1] if entry.repo else Path(entry.primary_path).stem
            quant = entry.quant or ""
            taken = self._taken()
            if quant:
                entry.aliases = [default_alias(repo_name, quant, entry.owner, taken)]
            else:
                entry.aliases = [default_alias(repo_name, "local", None, taken).removesuffix(":local")]
        self._models[entry.id] = entry
        self._save()
        return entry

    def set_aliases(self, model_id: str, aliases: list[str]) -> ModelEntry:
        entry = self._models.get(model_id)
        if entry is None:
            raise RegistryError(f"unknown model id {model_id!r}")
        if not aliases:
            raise RegistryError("a model needs at least one alias")
        taken = self._taken(exclude=model_id)
        for a in aliases:
            if not ALIAS_RE.match(a):
                raise RegistryError(f"invalid alias {a!r}: use letters, digits and . _ : @ / + -")
            if a in taken:
                raise RegistryError(f"alias {a!r} is already used by another model")
        if len(set(aliases)) != len(aliases):
            raise RegistryError("duplicate aliases")
        entry.aliases = list(aliases)
        self._save()
        return entry

    def update_meta(self, model_id: str, meta: dict) -> None:
        self._models[model_id].meta = meta
        self._save()

    def remove(self, model_id: str) -> ModelEntry | None:
        entry = self._models.pop(model_id, None)
        if entry is not None:
            self._save()
        return entry

    def scan(self, dirs: list[Path], location: str) -> list[ModelEntry]:
        """Register GGUF files found in `dirs` that aren't known yet. Returns the new entries."""
        known_paths = {os.path.realpath(p) for m in self._models.values() for p in m.paths}
        files: list[str] = []
        for d in dirs:
            d = Path(d)
            if not d.is_dir():
                continue
            for p in d.rglob("*.gguf"):
                if p.name.lower().startswith("mmproj"):
                    continue
                files.append(str(p))
        added = []
        for first, group in sorted(group_shards(files).items()):
            if os.path.realpath(first) in known_paths:
                continue
            try:
                meta = read_local_meta(Path(first))
            except (GGUFError, OSError):
                continue  # not a readable GGUF (or still being written)
            size = sum(os.path.getsize(f) for f in group)
            quant = quant_of(first)
            folder = Path(first).parent.name
            if "--" in folder and quant:
                # StrixRun's own <owner>--<repo>/ layout: same id as a fresh download
                owner, repo = folder.split("--", 1)
                entry = ModelEntry.from_download(owner, repo, quant, group, size, meta)
                entry.location = location
            else:
                stem = SHARD_RE.sub(r"\g<stem>", Path(first).name).removesuffix(".gguf")
                entry = ModelEntry(id=f"local--{_slug(stem)}", aliases=[], paths=group,
                                   size_bytes=size, location=location, meta=meta)
            added.append(self.add(entry))
        return added
