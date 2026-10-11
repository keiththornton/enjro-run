import { ChevronRight, ExternalLink, Search, X } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import { type ApiError, subscribe } from "../api/client";
import { fmtBytes } from "../api/format";
import { cancelDownload, listDownloads, repoFiles, searchHf, startDownload } from "../api/hf";
import type { DownloadJob, HfSearchResult, QuantRow, RepoListing } from "../api/types";

const VERDICT = { ok: "fits", tight: "tight", over: "too large" } as const;
const fitLabel = (fit: QuantRow["fit"]) =>
  fit && fit.verdict === "ok" && fit.placement === "split" ? "fits, partly CPU" : fit ? VERDICT[fit.verdict] : "";

/** Download jobs, kept live from the server's event stream. */
export function useDownloadJobs(): Record<string, DownloadJob> {
  const [jobs, setJobs] = useState<Record<string, DownloadJob>>({});
  useEffect(() => {
    listDownloads().then((d) => setJobs(Object.fromEntries(d.map((j) => [j.id, j])))).catch(() => {});
    return subscribe("/admin/downloads/events", {
      "download.progress": (d) => {
        const job = d as DownloadJob;
        setJobs((j) => ({ ...j, [job.id]: job }));
      },
    });
  }, []);
  return jobs;
}

export function HfSearch({ onClose, installed }: { onClose: () => void; installed: Set<string> }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<HfSearchResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);
  const jobs = useDownloadJobs();

  async function search(e: FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    setSearching(true);
    setError(null);
    try {
      setResults(await searchHf(query.trim()));
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setSearching(false);
    }
  }

  const active = Object.values(jobs).filter((j) => ["queued", "downloading", "error"].includes(j.status));
  return (
    <section className="hf-panel" aria-label="Find models on Hugging Face">
      <form className="hf-search" onSubmit={search} role="search">
        <Search aria-hidden className="hf-search-icon" />
        <input className="text-field" type="search" placeholder="Search GGUF models, e.g. qwen2.5 instruct"
          value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search Hugging Face" />
        <button type="submit" className="btn btn-md btn-secondary pressable" disabled={searching || !query.trim()}>
          {searching ? "Searching…" : "Search"}
        </button>
        <button type="button" className="icon-btn" aria-label="Close search" onClick={onClose}><X /></button>
      </form>
      {error && <p className="form-error" role="alert">{error}</p>}

      {active.length > 0 && (
        <ul className="downloads" aria-label="Downloads">
          {active.map((j) => <DownloadRow key={j.id} job={j} />)}
        </ul>
      )}

      {results && results.length === 0 && <p className="empty">No GGUF repos match “{query}”.</p>}
      {results && results.length > 0 && (
        <ul className="hf-results">
          {results.map((r) => <RepoRow key={r.repo} result={r} jobs={jobs} installed={installed} />)}
        </ul>
      )}
    </section>
  );
}

function RepoRow({ result, jobs, installed }: { result: HfSearchResult; jobs: Record<string, DownloadJob>; installed: Set<string> }) {
  const [open, setOpen] = useState(false);
  const [listing, setListing] = useState<RepoListing | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function toggle() {
    setOpen((o) => !o);
    if (!listing && !error) {
      try {
        setListing(await repoFiles(result.repo));
      } catch (e) {
        setError((e as ApiError).message);
      }
    }
  }

  return (
    <li className="hf-repo">
      <button type="button" className="hf-repo-head" aria-expanded={open} onClick={toggle}>
        <ChevronRight className="chevron" aria-hidden />
        <span className="mono hf-repo-name">{result.repo}</span>
        {result.gated && <span className="tag">gated</span>}
        <span className="mono meta hf-downloads">{result.downloads.toLocaleString()} downloads</span>
      </button>
      <div className="expand" data-open={open}>
        <div>
          {error && (
            <p className="form-error" role="alert">
              {error}{" "}
              {error.includes("gated") && (
                <a className="link-button" href={`https://huggingface.co/${result.repo}`} target="_blank" rel="noreferrer noopener">
                  Open on Hugging Face <ExternalLink className="inline-icon" />
                </a>
              )}
            </p>
          )}
          {open && !listing && !error && <p className="meta hf-loading"><span className="spinner" aria-hidden /> Reading model details…</p>}
          {listing && <QuantTable listing={listing} jobs={jobs} installed={installed} />}
        </div>
      </div>
    </li>
  );
}

export function QuantTable({ listing, jobs, installed }: { listing: RepoListing; jobs: Record<string, DownloadJob>; installed: Set<string> }) {
  const meta = listing.meta;
  return (
    <div className="quant-table">
      {meta && (
        <p className="mono meta quant-meta">
          {[meta.architecture, meta.parameters, meta.context_length ? `${meta.context_length.toLocaleString()} ctx` : null,
            meta.embedding_only ? "embedding" : null].filter(Boolean).join(" · ")}
        </p>
      )}
      {listing.quants.length === 0 ? (
        <p className="meta">This repo has no GGUF files Enjro Run can load.</p>
      ) : (
        <table>
          <thead>
            <tr><th scope="col">Quant</th><th scope="col">Size</th><th scope="col">Memory</th><th scope="col"><span className="sr-only">Action</span></th></tr>
          </thead>
          <tbody>
            {listing.quants.map((q) => (
              <QuantRowView key={q.quant} repo={listing.repo} q={q} downloaded={installed.has(q.model_id)}
                job={Object.values(jobs).find((j) => j.repo === listing.repo && j.quant === q.quant && j.status !== "cancelled")} />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function QuantRowView({ repo, q, job, downloaded }: { repo: string; q: QuantRow; job?: DownloadJob; downloaded: boolean }) {
  const [error, setError] = useState<string | null>(null);
  const inFlight = job && (job.status === "queued" || job.status === "downloading");
  async function download() {
    setError(null);
    try {
      await startDownload(repo, q.quant);
    } catch (e) {
      setError((e as ApiError).message);
    }
  }
  return (
    <tr>
      <td className="mono">{q.quant}{q.files.length > 1 && <span className="meta"> · {q.files.length} files</span>}</td>
      <td className="mono">{fmtBytes(q.size_bytes)}</td>
      <td>
        {q.fit ? (
          <span className={`fit fit-${q.fit.verdict}`} title={`${fmtBytes(q.fit.estimate_bytes)} needed of ${fmtBytes(q.fit.available_bytes)}`}>
            {fitLabel(q.fit)}
          </span>
        ) : <span className="meta">—</span>}
      </td>
      <td className="quant-action">
        {downloaded ? <span className="meta">Downloaded ✓</span>
          : inFlight ? <span className="mono meta">{Math.floor((100 * job.done_bytes) / Math.max(job.total_bytes, 1))}%</span>
          : <button type="button" className="btn btn-sm btn-secondary pressable" onClick={download}
              aria-label={`Download ${q.quant}, ${fmtBytes(q.size_bytes)}`}>Download</button>}
        {(error || job?.status === "error") && <p className="form-error" role="alert">{error ?? job?.error}</p>}
      </td>
    </tr>
  );
}

export function DownloadRow({ job }: { job: DownloadJob }) {
  const pct = Math.floor((100 * job.done_bytes) / Math.max(job.total_bytes, 1));
  const running = job.status === "queued" || job.status === "downloading";
  return (
    <li className="download">
      <div className="download-line">
        <span className="mono">{job.repo} <span className="meta">{job.quant}</span></span>
        <span className="mono meta">
          {job.status === "done" ? "Downloaded ✓" : job.status === "error" ? "Failed" : job.status === "cancelled" ? "Cancelled"
            : `${fmtBytes(job.done_bytes)} / ${fmtBytes(job.total_bytes)} · ${pct}%`}
        </span>
        {running && <button type="button" className="btn btn-sm btn-ghost pressable" onClick={() => cancelDownload(job.id)}>Cancel</button>}
      </div>
      {running && (
        <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label={`Downloading ${job.repo}`}>
          <span style={{ width: `${pct}%` }} />
        </div>
      )}
      {job.status === "error" && <p className="form-error">{job.error}</p>}
    </li>
  );
}
