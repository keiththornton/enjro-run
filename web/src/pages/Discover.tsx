import { ChevronRight, Download, ExternalLink, Heart } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import type { ApiError } from "../api/client";
import { fmtAge, fmtCount, fmtParams } from "../api/format";
import { discover, repoFiles } from "../api/hf";
import { listModels } from "../api/models";
import type { DiscoverRow, DownloadJob, RepoListing } from "../api/types";
import { DownloadRow, QuantTable, useDownloadJobs } from "../components/HfSearch";

const SORTS = [
  { value: "trending", label: "Trending" },
  { value: "downloads", label: "Most downloaded" },
  { value: "likes", label: "Most liked" },
  { value: "new", label: "New" },
] as const;
const KINDS = [
  { value: "all", label: "All" },
  { value: "chat", label: "Chat" },
  { value: "vision-chat", label: "Vision" },
  { value: "embedding", label: "Embedding" },
] as const;
const KIND_LABEL = { chat: "chat", "vision-chat": "vision", embedding: "embedding" } as const;
const HINT_LABEL = { likely: "fits at Q4", tight: "tight at Q4", unlikely: "too large at Q4" } as const;

export function DiscoverPage() {
  const [sort, setSort] = useState<string>("trending");
  const [kind, setKind] = useState<string>("all");
  const [fitsOnly, setFitsOnly] = useState(true);
  const [rows, setRows] = useState<DiscoverRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [installed, setInstalled] = useState<Set<string>>(new Set());
  const jobs = useDownloadJobs();

  const refreshInstalled = useCallback(() => listModels().then((m) => setInstalled(new Set(m.map((x) => x.id)))).catch(() => {}), []);
  useEffect(() => {
    refreshInstalled();
  }, [refreshInstalled]);
  useEffect(() => {
    if (Object.values(jobs).some((j) => j.status === "done")) refreshInstalled();
  }, [jobs, refreshInstalled]);

  useEffect(() => {
    let stale = false;
    setRows(null);
    setError(null);
    discover(sort, kind, fitsOnly)
      .then((r) => !stale && setRows(r))
      .catch((e: ApiError) => !stale && setError(e.message));
    return () => {
      stale = true;
    };
  }, [sort, kind, fitsOnly]);

  const active = Object.values(jobs).filter((j) => ["queued", "downloading", "error"].includes(j.status));

  return (
    <section className="page-body">
      <div className="page-head">
        <div>
          <h1 className="page-title">Discover</h1>
          <p className="page-sub">GGUF models on Hugging Face that StrixRun can run. Expand one to see every quant and whether it fits.</p>
        </div>
      </div>

      <div className="discover-controls">
        <div className="segmented" role="group" aria-label="Sort by">
          {SORTS.map((s) => (
            <button key={s.value} type="button" aria-pressed={sort === s.value} onClick={() => setSort(s.value)}>{s.label}</button>
          ))}
        </div>
        <div className="segmented" role="group" aria-label="Model type">
          {KINDS.map((k) => (
            <button key={k.value} type="button" aria-pressed={kind === k.value} onClick={() => setKind(k.value)}>{k.label}</button>
          ))}
        </div>
        <label className="fits-toggle">
          <input type="checkbox" className="checkbox" checked={fitsOnly} onChange={(e) => setFitsOnly(e.target.checked)} />
          Hide models too large for this machine
        </label>
      </div>

      {active.length > 0 && (
        <ul className="downloads discover-downloads" aria-label="Downloads">
          {active.map((j) => <DownloadRow key={j.id} job={j} />)}
        </ul>
      )}
      {error && <p className="form-error" role="alert">{error}</p>}
      {rows === null && !error && <p className="meta page-loading"><span className="spinner" aria-hidden /> Asking Hugging Face…</p>}
      {rows?.length === 0 && <p className="empty">Nothing here right now. Try another sort, or show models of every size.</p>}
      {rows && rows.length > 0 && (
        <ul className="discover-list">
          {rows.map((r) => <DiscoverItem key={r.repo} row={r} jobs={jobs} installed={installed} />)}
        </ul>
      )}
      <p className="settings-note discover-foot">
        Sizes come from the repo name; “fits at Q4” is a rough guide for a 4-bit quant on this machine’s memory.
        The quant table shows the exact estimate for every file.
      </p>
    </section>
  );
}

function DiscoverItem({ row, jobs, installed }: { row: DiscoverRow; jobs: Record<string, DownloadJob>; installed: Set<string> }) {
  const [open, setOpen] = useState(false);
  const [listing, setListing] = useState<RepoListing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [owner, name] = row.repo.split("/");
  const params = fmtParams(row.params_b);

  async function toggle() {
    setOpen((o) => !o);
    if (!listing && !error) {
      try {
        setListing(await repoFiles(row.repo));
      } catch (e) {
        setError((e as ApiError).message);
      }
    }
  }

  return (
    <li className="discover-item">
      <button type="button" className="discover-head" aria-expanded={open} onClick={toggle}>
        <ChevronRight className="chevron" aria-hidden />
        <span className="discover-name">
          <span className="mono discover-repo"><span className="meta">{owner}/</span>{name}</span>
          <span className="discover-tags">
            <span className="tag">{KIND_LABEL[row.kind]}</span>
            {params && <span className="tag mono">{params}</span>}
            {row.gated && <span className="tag">gated</span>}
            {row.fit_hint && <span className={`fit fit-${row.fit_hint === "likely" ? "ok" : row.fit_hint === "tight" ? "tight" : "over"}`}>{HINT_LABEL[row.fit_hint]}</span>}
          </span>
        </span>
        <span className="discover-stats mono">
          <span title={`${row.downloads.toLocaleString()} downloads in the last 30 days`}><Download aria-hidden /> {fmtCount(row.downloads)}</span>
          <span title={`${row.likes.toLocaleString()} likes`}><Heart aria-hidden /> {fmtCount(row.likes)}</span>
          <span className="meta" title={row.created_at ?? ""}>{fmtAge(row.created_at)}</span>
        </span>
      </button>
      <div className="expand" data-open={open}>
        <div>
          {error && <p className="form-error" role="alert">{error}</p>}
          {open && !listing && !error && <p className="meta hf-loading"><span className="spinner" aria-hidden /> Reading model details…</p>}
          {listing && <QuantTable listing={listing} jobs={jobs} installed={installed} />}
          {open && (
            <p className="discover-link">
              <a className="link-button" href={`https://huggingface.co/${row.repo}`} target="_blank" rel="noreferrer noopener">
                Model card on Hugging Face <ExternalLink className="inline-icon" />
              </a>
            </p>
          )}
        </div>
      </div>
    </li>
  );
}
