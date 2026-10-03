import * as Dialog from "@radix-ui/react-dialog";
import { Play, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { type ApiError, subscribe } from "../api/client";
import { fmtMs, fmtRate, fmtTime } from "../api/format";
import { listModels } from "../api/models";
import { getRequest, listRequests } from "../api/requests";
import type { ModelView, RequestDetail, RequestRow } from "../api/types";
import { navigate } from "../app/router";
import { replayFromRequest, setReplay } from "../app/replay";

const PAGE = 100;
const MONOGRAM = { chat: "CH", completions: "CO", embeddings: "EM", upstream: "UP" } as const;
const STATUSES = ["all", "ok", "error", "cancelled"] as const;

export function RequestsPage() {
  const [rows, setRows] = useState<RequestRow[] | null>(null);
  const [models, setModels] = useState<ModelView[]>([]);
  const [model, setModel] = useState("");
  const [status, setStatus] = useState<(typeof STATUSES)[number]>("all");
  const [live, setLive] = useState(false);
  const [more, setMore] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const filter = useMemo(() => ({ model_id: model || undefined, status: status === "all" ? undefined : status }), [model, status]);

  const reload = useCallback(async () => {
    try {
      const data = await listRequests({ limit: PAGE, ...filter });
      setRows(data);
      setMore(data.length === PAGE);
      setError(null);
    } catch (e) {
      setError((e as ApiError).message);
    }
  }, [filter]);

  useEffect(() => {
    reload();
  }, [reload]);

  useEffect(() => {
    listModels().then(setModels).catch(() => {});
  }, []);

  // Live arrivals: prepend rows that match the current filter.
  useEffect(() => {
    const unsubscribe = subscribe("/admin/requests/events", {
      "request.completed": (d) => {
        const row = d as RequestRow;
        if (filter.model_id && row.model_id !== filter.model_id) return;
        if (filter.status && row.status !== filter.status) return;
        setRows((r) => (r ? [row, ...r.filter((x) => x.id !== row.id)] : [row]));
      },
    }, undefined, setLive);
    return () => {
      setLive(false);
      unsubscribe();
    };
  }, [filter]);

  async function older() {
    if (!rows?.length) return;
    const data = await listRequests({ limit: PAGE, before: rows[rows.length - 1].ts, ...filter });
    setRows([...rows, ...data]);
    setMore(data.length === PAGE);
  }

  return (
    <section className="page-body">
      <div className="page-head">
        <h1 className="page-title">
          Requests {live && <span className="live" aria-label="Live"><span className="live-dot" /> live</span>}
        </h1>
        <div className="filters">
          <label className="sr-only" htmlFor="req-model">Model</label>
          <select id="req-model" className="text-field filter-select" value={model} onChange={(e) => setModel(e.target.value)}>
            <option value="">All models</option>
            {models.map((m) => <option key={m.id} value={m.id}>{m.aliases[0]}</option>)}
          </select>
          <div className="segmented" role="group" aria-label="Status">
            {STATUSES.map((s) => (
              <button key={s} type="button" aria-pressed={status === s} onClick={() => setStatus(s)}>{s}</button>
            ))}
          </div>
        </div>
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}

      {rows === null ? (
        <p className="meta page-loading">Loading requests…</p>
      ) : rows.length === 0 ? (
        <p className="empty">
          No requests yet. Point a client at <code className="mono">{window.location.origin}/v1</code> and they'll appear here as they finish.
        </p>
      ) : (
        <>
          <div className="rack" role="table" aria-label="Requests">
            <div className="rack-head" role="row">
              <span role="columnheader" className="sr-only">Endpoint</span>
              <span role="columnheader">Request</span>
              <span role="columnheader" className="num">TTFT</span>
              <span role="columnheader" className="num">tok/s</span>
              <span role="columnheader" className="num">tokens</span>
              <span role="columnheader" className="num">total</span>
            </div>
            {rows.map((r) => <RequestStrip key={r.id} row={r} onOpen={() => setSelected(r.id)} />)}
          </div>
          {more && <button type="button" className="btn btn-md btn-ghost pressable older" onClick={older}>Show older</button>}
        </>
      )}
      <RequestSheet id={selected} onClose={() => setSelected(null)} />
    </section>
  );
}

function RequestStrip({ row, onOpen }: { row: RequestRow; onOpen: () => void }) {
  const tokens = row.endpoint === "upstream" ? "—"
    : row.endpoint === "embeddings" ? `${row.prompt_tokens ?? "—"}`
    : `${row.prompt_tokens ?? "—"}→${row.completion_tokens ?? "—"}`;
  const label = `${row.alias_used ?? row.model_id}, ${row.endpoint}, ${row.status}, ${fmtTime(row.ts)}`;
  return (
    <button type="button" className="rack-row" role="row" data-status={row.status} onClick={onOpen} aria-label={label}>
      <span role="cell" className="mono-cell" data-status={row.status}>{MONOGRAM[row.endpoint]}</span>
      <span role="cell" className="rack-main">
        <span className="rack-line1">
          <span className="mono meta">{fmtTime(row.ts)}</span>
          <span className="mono rack-model">{row.alias_used ?? row.model_id}</span>
          {row.stream && <span className="tag">stream</span>}
        </span>
        <span className="rack-line2 mono">
          {row.status === "ok" ? (
            <span>{row.client_ip ?? "—"}{row.key_id ? ` · ${row.key_id}` : ""}{row.queue_ms && row.queue_ms > 50 ? ` · queued ${fmtMs(row.queue_ms)}` : ""}</span>
          ) : (
            <span className={row.status === "error" ? "danger" : "meta"}>{row.status === "cancelled" ? "client disconnected" : row.error ?? `HTTP ${row.http_status}`}</span>
          )}
        </span>
      </span>
      <span role="cell" className="num">{fmtMs(row.ttft_ms)}</span>
      <span role="cell" className="num">{fmtRate(row.tok_per_s)}</span>
      <span role="cell" className="num">{tokens}</span>
      <span role="cell" className="num">{fmtMs(row.total_ms)}</span>
    </button>
  );
}

function RequestSheet({ id, onClose }: { id: string | null; onClose: () => void }) {
  const [detail, setDetail] = useState<RequestDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setDetail(null);
    setError(null);
    if (id) getRequest(id).then(setDetail).catch((e: ApiError) => setError(e.message));
  }, [id]);

  const replay = detail && detail.endpoint === "chat" && detail.bodies_available
    ? replayFromRequest(detail.request_body, detail.alias_used ?? detail.model_id) : null;

  return (
    <Dialog.Root open={id !== null} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="sheet" aria-describedby={undefined}>
          <div className="sheet-inner">
            <header className="sheet-header">
              <div className="sheet-heading">
                <Dialog.Title className="dialog-title">{detail ? `${detail.alias_used ?? detail.model_id}` : "Request"}</Dialog.Title>
                {detail && <p className="sheet-sub mono">{detail.endpoint} · {fmtTime(detail.ts)} · {detail.id.slice(0, 12)}</p>}
              </div>
              <Dialog.Close className="icon-btn" aria-label="Close"><X /></Dialog.Close>
            </header>
            <div className="sheet-body">
              {error && <p className="form-error" role="alert">{error}</p>}
              {detail && (
                <>
                  <dl className="metrics mono">
                    <Metric label="status" value={detail.status === "ok" ? `ok · ${detail.http_status}` : detail.status} danger={detail.status === "error"} />
                    <Metric label="ttft" value={fmtMs(detail.ttft_ms)} />
                    <Metric label="tok/s" value={fmtRate(detail.tok_per_s)} />
                    <Metric label="total" value={fmtMs(detail.total_ms)} />
                    <Metric label="prompt" value={detail.prompt_tokens ?? "—"} />
                    <Metric label="completion" value={detail.completion_tokens ?? "—"} />
                    <Metric label="queued" value={fmtMs(detail.queue_ms)} />
                    <Metric label="client" value={`${detail.client_ip ?? "—"}${detail.key_id ? ` · ${detail.key_id}` : ""}`} />
                  </dl>
                  {detail.error && <p className="form-error">{detail.error}</p>}
                  {detail.bodies_available ? (
                    <>
                      <h3 className="eyebrow body-label">Request</h3>
                      <pre className="code-block">{pretty(detail.request_body)}</pre>
                      <h3 className="eyebrow body-label">Response</h3>
                      <pre className="code-block">{pretty(detail.response_body)}</pre>
                    </>
                  ) : (
                    <p className="meta body-note">
                      Bodies are kept in memory for recent requests only; this one has rotated out.
                    </p>
                  )}
                </>
              )}
            </div>
            <footer className="sheet-footer">
              <Dialog.Close className="btn btn-md btn-ghost pressable">Close</Dialog.Close>
              {replay && (
                <button type="button" className="btn btn-md btn-secondary pressable" onClick={() => {
                  setReplay(replay);
                  onClose();
                  navigate("playground");
                }}>
                  <Play /> Replay in playground
                </button>
              )}
            </footer>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function Metric({ label, value, danger }: { label: string; value: string | number; danger?: boolean }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd className={danger ? "danger" : undefined}>{value}</dd>
    </div>
  );
}

/** Bodies are untrusted: rendered as text only, never as HTML. */
function pretty(body: unknown): string {
  if (body == null) return "—";
  if (typeof body === "string") return body;
  return JSON.stringify(body, null, 2);
}
