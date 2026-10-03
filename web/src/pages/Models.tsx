import * as Dialog from "@radix-ui/react-dialog";
import { HardDrive, Pencil, Search, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { type ApiError, subscribe } from "../api/client";
import { fmtBytes, fmtCtx, fmtDuration } from "../api/format";
import { deleteModel, listModels, listPs, setAliases, unloadModel } from "../api/models";
import type { ModelView, PsEntry } from "../api/types";
import { HfSearch } from "../components/HfSearch";
import { LoadDrawer } from "../components/LoadDrawer";

const LIVE = new Set(["loading", "ready", "stopping"]);

export function ModelsPage() {
  const [models, setModels] = useState<ModelView[] | null>(null);
  const [ps, setPs] = useState<Record<string, PsEntry>>({});
  const [logs, setLogs] = useState<Record<string, string[]>>({});
  const [loading, setLoading] = useState<string | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [m, p] = await Promise.all([listModels(), listPs()]);
      setModels(m);
      setPs(Object.fromEntries(p.map((e) => [e.id, e])));
      setError(null);
    } catch (e) {
      setError((e as ApiError).message);
    }
  }, []);

  useEffect(() => {
    refresh();
    const tick = setInterval(refresh, 5000); // uptime, memory and queue depth
    const unsubscribe = subscribe("/admin/models/events", {
      "model.state": () => refresh(),
      "model.log": (d) => {
        const { id, line } = d as { id: string; line: string };
        setLogs((l) => ({ ...l, [id]: [...(l[id] ?? []).slice(-499), line] }));
      },
    });
    const downloads = subscribe("/admin/downloads/events", {
      "download.progress": (d) => (d as { status: string }).status === "done" && refresh(),
    });
    return () => {
      clearInterval(tick);
      unsubscribe();
      downloads();
    };
  }, [refresh]);

  function openLoad(m: ModelView) {
    setLogs((l) => ({ ...l, [m.id]: [] }));
    setLoading(m.id);
  }

  const crashed = models?.filter((m) => m.state.status === "crashed") ?? [];
  const loaded = models?.filter((m) => LIVE.has(m.state.status)) ?? [];
  const onDisk = models?.filter((m) => !LIVE.has(m.state.status) && m.state.status !== "crashed") ?? [];
  const drawerModel = models?.find((m) => m.id === loading) ?? null;

  return (
    <section className="page-body">
      <div className="page-head">
        <h1 className="page-title">Models</h1>
        <button type="button" className="btn btn-md btn-secondary pressable" onClick={() => setSearchOpen((o) => !o)}
          aria-expanded={searchOpen}>
          <Search /> Find on Hugging Face
        </button>
      </div>

      {error && <p className="form-error" role="alert">{error}</p>}

      <div className="expand" data-open={searchOpen || models?.length === 0}>
        <div><HfSearch onClose={() => setSearchOpen(false)} installed={new Set(models?.map((m) => m.id))} /></div>
      </div>

      {models === null ? (
        <p className="meta page-loading">Loading models…</p>
      ) : models.length === 0 ? (
        <p className="empty">No models yet. Search Hugging Face above for a GGUF model to download.</p>
      ) : (
        <>
          <Bay label="Needs attention" count={crashed.length}>
            {crashed.map((m) => (
              <ModelStrip key={m.id} model={m} onLoad={() => openLoad(m)} onChanged={refresh} />
            ))}
          </Bay>
          <Bay label="Loaded" count={loaded.length} empty="Nothing loaded. Load a model to serve it at /v1.">
            {loaded.map((m) => (
              <ModelStrip key={m.id} model={m} ps={ps[m.id]} onLoad={() => openLoad(m)} onChanged={refresh} />
            ))}
          </Bay>
          <Bay label="On disk" count={onDisk.length}>
            {onDisk.map((m) => (
              <ModelStrip key={m.id} model={m} onLoad={() => openLoad(m)} onChanged={refresh} />
            ))}
          </Bay>
        </>
      )}

      <LoadDrawer model={drawerModel} logs={drawerModel ? (logs[drawerModel.id] ?? []) : []} onClose={() => setLoading(null)} />
    </section>
  );
}

function Bay({ label, count, empty, children }: { label: string; count: number; empty?: string; children: React.ReactNode }) {
  if (count === 0 && !empty) return null;
  return (
    <section className="bay" aria-label={label}>
      <h2 className="bay-head">
        <span className="eyebrow">{label}</span>
        <span className="eyebrow bay-count" aria-live="polite">{count}</span>
      </h2>
      {count === 0 && empty ? <p className="bay-empty">{empty}</p> : <ul className="strips">{children}</ul>}
    </section>
  );
}

function StateGlyph({ status }: { status: string }) {
  const label = { ready: "Serving", loading: "Loading", stopping: "Stopping", crashed: "Crashed", stopped: "Not loaded" }[status] ?? status;
  return (
    <span className="glyph" data-status={status} role="img" aria-label={label} title={label}>
      {status === "loading" || status === "stopping" ? <span className="spinner" /> : <span className="dot" />}
    </span>
  );
}

function ModelStrip({ model, ps, onLoad, onChanged }: { model: ModelView; ps?: PsEntry; onLoad: () => void; onChanged: () => void }) {
  const { status, reason } = model.state;
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const live = LIVE.has(status);

  async function unload() {
    setBusy(true);
    try {
      await unloadModel(model.id);
      onChanged();
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  const facts = live && ps
    ? [`ctx ${fmtCtx(ps.ctx_per_request ?? 0)} × ${ps.parallel}`, `queue ${ps.queue_depth}`,
       ps.uptime_s != null ? `up ${fmtDuration(ps.uptime_s)}` : null, ps.rss_bytes ? `${fmtBytes(ps.rss_bytes)} rss` : null]
    : [model.quant, model.meta.architecture, model.meta.parameters, model.meta.context_length ? `${fmtCtx(model.meta.context_length)} ctx` : null,
       model.preset.mode === "embedding" ? "embedding" : null];

  return (
    <li className="strip" data-status={status}>
      <StateGlyph status={status} />
      <div className="strip-main">
        <div className="strip-line1">
          <span className="strip-title mono" title={model.id}>{model.aliases[0]}</span>
          {model.aliases.length > 1 && <span className="chip">+{model.aliases.length - 1}</span>}
          {model.location === "scan" && <span className="tag" title="Read-only folder">
            <HardDrive /> scanned
          </span>}
          <span className="strip-size mono">{fmtBytes(model.size_bytes)}</span>
        </div>
        <div className="strip-line2 mono">
          {status === "crashed" ? (
            <span className="danger">{reason}</span>
          ) : (
            facts.filter(Boolean).map((f, i) => <span key={i}>{f}</span>)
          )}
        </div>
        {error && <p className="form-error" role="alert">{error}</p>}
        {editing && <AliasEditor model={model} onDone={() => { setEditing(false); onChanged(); }} />}
      </div>
      <div className="strip-actions">
        <button type="button" className="icon-btn" aria-label={`Edit aliases for ${model.aliases[0]}`} onClick={() => setEditing((e) => !e)}>
          <Pencil />
        </button>
        {!live && <DeleteButton model={model} onDeleted={onChanged} />}
        {live ? (
          <button type="button" className="btn btn-sm btn-secondary pressable" onClick={unload} disabled={busy || status === "stopping"}>
            {status === "loading" ? "Cancel" : "Unload"}
          </button>
        ) : (
          <button type="button" className="btn btn-sm btn-secondary pressable" onClick={onLoad}>
            {status === "crashed" ? "Reload" : "Load"}
          </button>
        )}
      </div>
    </li>
  );
}

function AliasEditor({ model, onDone }: { model: ModelView; onDone: () => void }) {
  const [value, setValue] = useState(model.aliases.join(", "));
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.focus(), []);
  async function save(e: React.FormEvent) {
    e.preventDefault();
    try {
      await setAliases(model.id, value.split(",").map((a) => a.trim()).filter(Boolean));
      onDone();
    } catch (err) {
      setError((err as ApiError).message);
    }
  }
  return (
    <form className="alias-editor" onSubmit={save}>
      <label className="field-label" htmlFor={`aliases-${model.id}`}>Names clients can use in the <code>model</code> field, comma separated</label>
      <div className="alias-row">
        <input id={`aliases-${model.id}`} ref={input} className="text-field text-field-mono" value={value}
          onChange={(e) => setValue(e.target.value)} spellCheck={false} />
        <button type="submit" className="btn btn-md btn-secondary pressable">Save names</button>
        <button type="button" className="btn btn-md btn-ghost pressable" onClick={onDone}>Cancel</button>
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}
    </form>
  );
}

function DeleteButton({ model, onDeleted }: { model: ModelView; onDeleted: () => void }) {
  const [error, setError] = useState<string | null>(null);
  if (model.location !== "store") return null;
  async function remove() {
    try {
      await deleteModel(model.id);
      onDeleted();
    } catch (e) {
      setError((e as ApiError).message);
    }
  }
  return (
    <Dialog.Root onOpenChange={() => setError(null)}>
      <Dialog.Trigger className="icon-btn" aria-label={`Delete ${model.aliases[0]}`}>
        <Trash2 />
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog confirm-dialog">
          <Dialog.Title className="dialog-title">Delete {model.aliases[0]}?</Dialog.Title>
          <Dialog.Description className="dialog-description">
            This removes {fmtBytes(model.size_bytes)} of model files from disk. You can download it again later.
          </Dialog.Description>
          {error && <p className="form-error" role="alert">{error}</p>}
          <div className="dialog-actions">
            <Dialog.Close className="btn btn-md btn-ghost pressable">Keep</Dialog.Close>
            <button type="button" className="btn btn-md btn-danger pressable" onClick={remove}>Delete files</button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

