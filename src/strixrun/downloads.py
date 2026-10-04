"""Hugging Face search, quant listings with fit badges, and download jobs.

Downloads run one file at a time in a killable worker process (see hf_worker).
"""

import asyncio
import json
import logging
import re
import shutil
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from strixrun import errors
from strixrun.backend import procutil
from strixrun.events import EventBus
from strixrun.gguf_meta import GGUFError, read_local_meta, read_remote_meta
from strixrun.presets import default_preset
from strixrun.registry import (
    ModelEntry,
    Registry,
    group_shards,
    is_projector,
    make_id,
    pick_projector,
    quant_of,
)

log = logging.getLogger(__name__)

CACHE_TTL_S = 600
DISK_HEADROOM = 1.05



class DownloadError(RuntimeError):
    pass


# ---- Hugging Face access (faked in tests) -----------------------------------------------


class HubClient:
    """Thin wrapper over huggingface_hub; all calls are blocking (run in threads)."""

    def search(self, query: str, limit: int) -> list[dict]:
        from huggingface_hub import HfApi

        models = HfApi().list_models(search=query, filter="gguf", sort="downloads", limit=limit)
        return [{"repo": m.id, "downloads": m.downloads, "likes": m.likes,
                 "updated": m.last_modified.isoformat() if m.last_modified else None,
                 "gated": bool(getattr(m, "gated", False))} for m in models]

    def list_gguf(self, sort: str, limit: int) -> list[dict]:
        from huggingface_hub import HfApi

        hf_sort = {"trending": "trending_score", "downloads": "downloads", "likes": "likes"}[sort]
        models = HfApi().list_models(
            filter="gguf", sort=hf_sort, limit=limit,
            expand=["downloads", "likes", "createdAt", "lastModified", "trendingScore", "pipeline_tag", "gated"],
        )
        return [{
            "repo": m.id,
            "downloads": m.downloads or 0,
            "likes": m.likes or 0,
            "trending_score": getattr(m, "trending_score", None) or 0,
            "pipeline_tag": m.pipeline_tag,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "last_modified": m.last_modified.isoformat() if m.last_modified else None,
            "gated": bool(m.gated),
        } for m in models]

    def repo_files(self, repo: str) -> dict:
        from huggingface_hub import HfApi

        info = HfApi().model_info(repo, files_metadata=True)
        return {
            "gated": bool(info.gated),
            "files": [(s.rfilename, s.size or 0) for s in info.siblings or []],
        }

    async def remote_meta(self, repo: str, filename: str) -> dict:
        import httpx
        from huggingface_hub import get_token, hf_hub_url

        token = get_token()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            return await read_remote_meta(hf_hub_url(repo, filename), client, headers)


def hub_error(e: Exception, repo: str) -> errors.StrixError:
    """Map huggingface_hub failures to user-facing errors."""
    name = type(e).__name__
    text = str(e)
    if name == "GatedRepoError" or "gated" in text.lower() or " 401" in text or "403" in text:
        err = errors.Forbidden(
            f"{repo} is gated: accept its license at https://huggingface.co/{repo} "
            "and set a Hugging Face token in Settings"
        )
        err.code = "gated_repo"
        return err
    if "429" in text or "rate limit" in text.lower():
        err = errors.StrixError(
            "Hugging Face is rate-limiting requests; set a Hugging Face token in Settings "
            "for higher limits, or retry in a few minutes"
        )
        err.status, err.type, err.code = 429, "rate_limit_error", "hf_rate_limited"
        return err
    if name in ("RepositoryNotFoundError", "RevisionNotFoundError") or " 404" in text:
        return errors.NotFound(f"Hugging Face repo {repo}")
    return errors.UpstreamError(repo, f"Hugging Face request failed: {text[:200]}")


# ---- download worker plumbing -----------------------------------------------------------


async def download_file(repo: str, filename: str, local_dir: str,
                        on_progress: Callable[[int, int | None], None],
                        cancel: asyncio.Event, fake_size: int | None = None) -> str:
    """Download one file via a watchdog-wrapped worker process. Raises DownloadError."""
    cmd = [sys.executable, "-m", "strixrun.hf_worker", repo, filename, local_dir]
    if fake_size is not None:
        cmd += ["--fake-size", str(fake_size)]
    proc = await procutil.spawn(cmd)
    cancel_wait = asyncio.create_task(cancel.wait())
    result: dict = {}
    try:
        while True:
            read = asyncio.create_task(proc.stdout.readline())
            done, _ = await asyncio.wait({read, cancel_wait}, return_when=asyncio.FIRST_COMPLETED)
            if cancel_wait in done:
                read.cancel()
                await procutil.stop(proc, grace=2)
                raise DownloadError("cancelled")
            line = read.result()
            if not line:
                break
            try:
                msg = json.loads(line)
            except ValueError:
                continue  # library warnings on stderr/stdout
            if "done" in msg:
                on_progress(int(msg["done"]), msg.get("total"))
            else:
                result = msg
    finally:
        cancel_wait.cancel()
        if proc.returncode is None:
            await procutil.stop(proc, grace=2)
    if "path" in result:
        return result["path"]
    raise DownloadError(result.get("error", "download worker exited without a result"))


# ---- jobs -------------------------------------------------------------------------------


@dataclass
class Job:
    repo: str
    quant: str
    files: list[tuple[str, int]]
    target_dir: str
    kind: str = "model"  # model | projector (a vision projector for a model already on disk)
    projector: tuple[str, int] | None = None  # fetched after the model files
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "queued"  # queued | downloading | done | error | cancelled
    done_bytes: int = 0
    total_bytes: int = 0
    error: str | None = None
    warning: str | None = None  # finished, but something optional (the projector) failed
    model_id: str | None = None
    started_at: float = field(default_factory=time.time)

    def view(self) -> dict:
        d = asdict(self)
        d["files"] = [name for name, _ in self.files]
        d["projector"] = self.projector[0] if self.projector else None
        return d


class Downloads:
    def __init__(self, bus: EventBus, registry: Registry, store_dir: Callable[[], Path],
                 estimator=None, hub: HubClient | None = None, fetch=download_file) -> None:
        self.bus = bus
        self.registry = registry
        self.store_dir = store_dir
        self.estimator = estimator
        self.hub = hub or HubClient()
        self._fetch = fetch
        self._cache: dict[tuple, tuple[float, object]] = {}
        self.jobs: dict[str, Job] = {}
        self._cancel: dict[str, asyncio.Event] = {}
        self._tasks: set[asyncio.Task] = set()

    async def _cached(self, key: tuple, fn, *args):
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_TTL_S:
            return hit[1]
        value = await fn(*args)
        self._cache[key] = (time.monotonic(), value)
        return value

    async def search(self, query: str, limit: int = 30) -> list[dict]:
        try:
            return await self._cached(("search", query, limit), asyncio.to_thread, self.hub.search, query, limit)
        except Exception as e:  # mapped to an HTTP error
            raise hub_error(e, query) from e

    async def _repo(self, repo: str) -> dict:
        try:
            return await self._cached(("repo", repo), asyncio.to_thread, self.hub.repo_files, repo)
        except Exception as e:  # mapped to an HTTP error
            raise hub_error(e, repo) from e

    def _quant_groups(self, files: list[tuple[str, int]]) -> dict[str, list[tuple[str, int]]]:
        sizes = dict(files)
        ggufs = [f for f, _ in files if f.lower().endswith(".gguf") and not is_projector(f)]
        groups: dict[str, list[tuple[str, int]]] = {}
        for first, members in group_shards(ggufs).items():
            quant = quant_of(first)
            if quant is None:
                continue
            key = quant if quant not in groups else f"{quant} ({Path(first).stem})"
            groups[key] = [(m, sizes[m]) for m in members]
        return groups

    @staticmethod
    def _projector(files: list[tuple[str, int]]) -> tuple[str, int] | None:
        name = pick_projector([f for f, _ in files])
        return (name, dict(files)[name]) if name else None

    async def repo_listing(self, repo: str) -> dict:
        info = await self._repo(repo)
        groups = self._quant_groups(info["files"])
        projector = self._projector(info["files"])
        projector_size = projector[1] if projector else 0
        meta = None
        if groups:
            smallest = min(groups.values(), key=lambda fs: sum(s for _, s in fs))
            try:
                meta = await self._cached(("meta", repo), self.hub.remote_meta, repo, smallest[0][0])
            except (GGUFError, Exception):  # noqa: BLE001 - badges are best-effort
                meta = None
        owner, name = repo.split("/", 1)
        quants = []
        for quant, members in sorted(groups.items(), key=lambda kv: sum(s for _, s in kv[1])):
            size = sum(s for _, s in members)
            model_id = make_id(owner, name, quant)
            fit = None
            if meta and self.estimator:
                # The projector downloads (and, by default, loads) with every quant.
                probe_entry = ModelEntry(id=model_id, aliases=[], paths=[],
                                         size_bytes=size + projector_size, meta=meta)
                fit = self.estimator.estimate(probe_entry, default_preset(meta))
            quants.append({
                "quant": quant,
                "files": [m for m, _ in members],
                "size_bytes": size,
                "model_id": model_id,
                "downloaded": self.registry.get(model_id) is not None,
                "fit": fit,
            })
        return {
            "repo": repo, "gated": info["gated"], "meta": meta, "quants": quants,
            "projector": {"file": projector[0], "size_bytes": projector[1]} if projector else None,
        }

    # -- jobs ---------------------------------------------------------------------------

    async def start(self, repo: str, quant: str) -> Job:
        if not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", repo):
            raise errors.BadRequest(f"invalid repo name {repo!r}")
        info = await self._repo(repo)
        groups = self._quant_groups(info["files"])
        if quant not in groups:
            raise errors.BadRequest(f"{repo} has no {quant} GGUF; choose one of {', '.join(groups)}")
        owner, name = repo.split("/", 1)
        model_id = make_id(owner, name, quant)
        if self.registry.get(model_id):
            raise errors.Conflict(f"{repo} {quant} is already downloaded")
        if any(j.status in ("queued", "downloading") and j.repo == repo and j.quant == quant
               for j in self.jobs.values()):
            raise errors.Conflict(f"{repo} {quant} is already downloading")

        target = self._target(repo)
        files = groups[quant]
        projector = self._projector(info["files"])
        for filename, _ in files + ([projector] if projector else []):
            self._check_path(target, filename, repo)
        await self._check_header(repo, files[0][0])
        if projector and _on_disk(target, *projector):
            projector = None  # another quant of this repo already brought it
        total = sum(s for _, s in files) + (projector[1] if projector else 0)
        self._check_space(target, total, f"{repo} {quant}")

        job = Job(repo=repo, quant=quant, files=files, target_dir=str(target), total_bytes=total,
                  model_id=model_id, projector=projector)
        return self._launch(job)

    async def start_projector(self, model_id: str) -> Job:
        """Fetch the vision projector for a model downloaded without one."""
        entry = self.registry.get(model_id)
        if entry is None:
            raise errors.NotFound(f"model {model_id}")
        if not entry.repo:
            raise errors.BadRequest(
                "StrixRun doesn't know which Hugging Face repo this model came from; put its "
                "mmproj file in the model's folder and restart StrixRun"
            )
        if entry.mmproj:
            raise errors.Conflict(f"{entry.aliases[0]} already has a vision projector")
        if any(j.status in ("queued", "downloading") and j.repo == entry.repo and
               (j.kind == "projector" or j.projector) for j in self.jobs.values()):
            raise errors.Conflict(f"the projector for {entry.repo} is already downloading")
        info = await self._repo(entry.repo)
        projector = self._projector(info["files"])
        if projector is None:
            raise errors.NotFound(f"a vision projector in {entry.repo}; this model reads text only")
        target = self._target(entry.repo)
        self._check_path(target, projector[0], entry.repo)
        if not _on_disk(target, *projector):
            self._check_space(target, projector[1], f"the {entry.repo} projector")
        job = Job(repo=entry.repo, quant="projector", files=[], target_dir=str(target), kind="projector",
                  projector=projector, total_bytes=projector[1], model_id=model_id)
        return self._launch(job)

    def _target(self, repo: str) -> Path:
        owner, name = repo.split("/", 1)
        return (Path(self.store_dir()).resolve() / f"{owner}--{name}").resolve()

    def _check_path(self, target: Path, filename: str, repo: str) -> None:
        store = Path(self.store_dir()).resolve()
        dest = (target / filename).resolve()
        if store not in dest.parents or ".." in Path(filename).parts or Path(filename).is_absolute():
            raise errors.BadRequest(f"refusing file path {filename!r} from {repo}")

    def _check_space(self, target: Path, total: int, what: str) -> None:
        target.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(target).free
        if free < total * DISK_HEADROOM:
            err = errors.StrixError(
                f"not enough disk space: {what} needs {total / 1e9:.1f} GB, "
                f"{free / 1e9:.1f} GB free in {Path(self.store_dir()).resolve()}"
            )
            err.status, err.type, err.code = 507, "server_error", "insufficient_storage"
            raise err

    def _launch(self, job: Job) -> Job:
        self.jobs[job.id] = job
        self._cancel[job.id] = asyncio.Event()
        task = asyncio.create_task(self._run(job))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        self._publish(job)
        return job

    async def _check_header(self, repo: str, filename: str) -> None:
        """Refuse uploads whose header isn't a GGUF before spending gigabytes on them.

        Costs one small range request. If Hugging Face can't be reached for the check,
        the download goes ahead and the file is checked again once it's on disk.
        """
        try:
            await self.hub.remote_meta(repo, filename)
        except GGUFError as e:
            err = errors.StrixError(_damaged_message(filename, str(e)))
            err.status, err.type, err.code = 422, "invalid_request_error", "damaged_gguf"
            raise err from e
        except Exception:  # best-effort pre-check; the on-disk check still runs
            log.debug("header pre-check for %s/%s failed", repo, filename, exc_info=True)

    def cancel(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if job is None:
            raise errors.NotFound(f"download {job_id}")
        if job.status in ("queued", "downloading"):
            self._cancel[job_id].set()
        return job

    def _publish(self, job: Job) -> None:
        self.bus.publish("download.progress", job.view())

    async def _run(self, job: Job) -> None:
        job.status = "downloading"
        self._publish(job)
        completed = 0
        last_pub = 0.0

        async def fetch(filename: str, size: int) -> str:
            nonlocal completed

            def progress(done: int, total: int | None, base=completed) -> None:
                nonlocal last_pub
                job.done_bytes = base + min(done, size or done)
                now = time.monotonic()
                if now - last_pub > 0.25:
                    last_pub = now
                    self._publish(job)

            path = await self._fetch(job.repo, filename, job.target_dir, progress, self._cancel[job.id])
            completed += size
            job.done_bytes = completed
            return path

        try:
            if job.kind == "projector":
                path = (await fetch(*job.projector) if not _on_disk(Path(job.target_dir), *job.projector)
                        else str(Path(job.target_dir) / job.projector[0]))
                for entry in self.registry.all():
                    if entry.repo == job.repo and not entry.mmproj:
                        self.registry.set_projector(entry.id, path)
                job.status = "done"
                return
            paths = [await fetch(filename, size) for filename, size in job.files]
            meta = await asyncio.to_thread(read_local_meta, Path(paths[0]))
            owner, name = job.repo.split("/", 1)
            model_bytes = sum(size for _, size in job.files)
            entry = ModelEntry.from_download(owner, name, job.quant, paths, model_bytes, meta)
            if job.projector:
                try:
                    entry.mmproj = await fetch(*job.projector)
                except DownloadError as e:
                    if str(e) == "cancelled":
                        raise
                    job.warning = f"downloaded without its vision projector: {_friendly(str(e), job.repo)}"
            self.registry.add(entry)
            self.registry.attach_projectors()  # a projector another quant already brought
            job.model_id = entry.id
            job.status = "done"
        except DownloadError as e:
            job.status = "cancelled" if str(e) == "cancelled" else "error"
            job.error = None if job.status == "cancelled" else _friendly(str(e), job.repo)
        except (GGUFError, OSError) as e:
            job.status, job.error = "error", _damaged_message(job.files[0][0], str(e))
            _remove_files(job.target_dir, [name for name, _ in job.files])
        finally:
            self._publish(job)

    async def aclose(self) -> None:
        for ev in self._cancel.values():
            ev.set()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)


def _on_disk(target: Path, filename: str, size: int) -> bool:
    path = target / filename
    try:
        return path.is_file() and path.stat().st_size == size
    except OSError:
        return False


def _damaged_message(filename: str, detail: str) -> str:
    return (f"{Path(filename).name} on Hugging Face isn't a valid GGUF file ({detail}); the upload "
            "looks damaged. Try another quant from this repo.")


def _remove_files(target_dir: str, names: list[str]) -> None:
    root = Path(target_dir).resolve()
    for name in names:
        path = (root / name).resolve()
        if root in path.parents:
            path.unlink(missing_ok=True)


def _friendly(message: str, repo: str) -> str:
    if "GatedRepoError" in message or "401" in message or "403" in message:
        return (f"{repo} is gated: accept its license at https://huggingface.co/{repo} "
                "and set a Hugging Face token in Settings")
    if "429" in message:
        return "Hugging Face rate limit reached; set a Hugging Face token in Settings or retry later"
    return message
