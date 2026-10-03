"""Hugging Face search, repo quant listings with fit badges, and download jobs."""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from strixrun.api.deps import Ctx
from strixrun.api.sse import sse_response

router = APIRouter()


@router.get("/hf/search")
async def hf_search(ctx: Ctx, q: str, limit: int = 30):
    return {"data": await ctx.downloads.search(q, min(max(limit, 1), 100))}


@router.get("/hf/repo/{owner}/{repo}/files")
async def hf_repo_files(owner: str, repo: str, ctx: Ctx):
    return await ctx.downloads.repo_listing(f"{owner}/{repo}")


class DownloadIn(BaseModel):
    repo: str
    quant: str


@router.post("/downloads", status_code=202)
async def start_download(body: DownloadIn, ctx: Ctx):
    return (await ctx.downloads.start(body.repo, body.quant)).view()


@router.get("/downloads")
def list_downloads(ctx: Ctx):
    return {"data": [j.view() for j in ctx.downloads.jobs.values()]}


@router.get("/downloads/events")
def download_events(request: Request, ctx: Ctx):
    return sse_response(request, ctx.bus, {"download.progress"})


@router.delete("/downloads/{job_id}")
def cancel_download(job_id: str, ctx: Ctx):
    return ctx.downloads.cancel(job_id).view()
