import { Check, Copy } from "lucide-react";
import { type ReactNode, useCallback, useEffect, useState } from "react";
import { type ApiError, subscribe } from "../api/client";
import { fmtBytes } from "../api/format";
import { apiKey, getBinary, getSettings, getSystem, installBinary, rollbackBinary, setHfToken, updateSettings } from "../api/settings";
import type { BinaryStatus, Settings, SystemInfo } from "../api/types";

export function SettingsPage() {
  const [s, setS] = useState<Settings | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => getSettings().then(setS).catch((e: ApiError) => setError(e.message)), []);
  useEffect(() => {
    load();
  }, [load]);

  async function patch(p: Partial<Settings>) {
    setError(null);
    try {
      setS(await updateSettings(p));
    } catch (e) {
      setError((e as ApiError).message);
    }
  }

  if (!s) return <section className="page-body"><h1 className="page-title">Settings</h1>{error && <p className="form-error">{error}</p>}</section>;
  return (
    <section className="page-body settings">
      <h1 className="page-title">Settings</h1>
      {error && <p className="form-error" role="alert">{error}</p>}
      <Access s={s} onChange={setS} onPatch={patch} />
      <HfToken s={s} onChange={load} />
      <Section title="Request log" note="Request bodies are kept in memory only and never written to disk.">
        <NumberRow label="Keep request history for" unit="days" value={s.log_retention_days} min={1} onSave={(v) => patch({ log_retention_days: v })} />
        <NumberRow label="Keep bodies of the last" unit="requests" value={s.log_body_ring_size} min={0}
          hint="0 keeps no prompts or responses at all" onSave={(v) => patch({ log_body_ring_size: v })} />
      </Section>
      <Section title="Models">
        <Toggle label="Reload models that were loaded when StrixRun stopped" checked={s.restore_loaded} onChange={(v) => patch({ restore_loaded: v })} />
        <NumberRow label="Stop a response after the model is silent for" unit="seconds" value={s.upstream_idle_timeout_s} min={1}
          onSave={(v) => patch({ upstream_idle_timeout_s: v })} />
        <FileOnly label="Model folder" value={s.models_dirs[0]} command="strixrun config set models_dirs <path>" />
        <FileOnly label="Also read models from" value={s.extra_scan_dirs.join(", ") || "none"} command="strixrun config set extra_scan_dirs <path>[,<path>]" />
      </Section>
      <Machine />
      <Binary />
    </section>
  );
}

function Section({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section className="settings-section" aria-label={title}>
      <h2 className="eyebrow">{title}</h2>
      {note && <p className="settings-note">{note}</p>}
      <div className="settings-rows">{children}</div>
    </section>
  );
}

function Row({ label, hint, children }: { label: ReactNode; hint?: ReactNode; children?: ReactNode }) {
  return (
    <div className="settings-row">
      <div className="settings-label">
        <span>{label}</span>
        {hint && <span className="field-hint">{hint}</span>}
      </div>
      <div className="settings-control">{children}</div>
    </div>
  );
}

function FileOnly({ label, value, command }: { label: string; value: string; command: string }) {
  return (
    <Row label={label} hint={<>Change with <code className="mono">{command}</code></>}>
      <span className="mono settings-value">{value}</span>
    </Row>
  );
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="settings-row toggle-row">
      <span className="settings-label"><span>{label}</span></span>
      <input type="checkbox" className="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
    </label>
  );
}

function NumberRow({ label, unit, value, min, hint, onSave }: {
  label: string; unit: string; value: number; min: number; hint?: string; onSave: (v: number) => void;
}) {
  const [v, setV] = useState(String(value));
  useEffect(() => setV(String(value)), [value]);
  const id = `n-${label.replace(/\W+/g, "-")}`;
  const commit = () => {
    const n = Math.max(min, Math.round(Number(v)));
    if (Number.isFinite(n) && n !== value) onSave(n);
    else setV(String(value));
  };
  return (
    <Row label={<label htmlFor={id}>{label}</label>} hint={hint}>
      <span className="number-unit">
        <input id={id} className="text-field text-field-mono" type="number" min={min} value={v}
          onChange={(e) => setV(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === "Enter" && commit()} />
        <span className="meta">{unit}</span>
      </span>
    </Row>
  );
}

function Access({ s, onChange, onPatch }: { s: Settings; onChange: (s: Settings) => void; onPatch: (p: Partial<Settings>) => void }) {
  const [shown, setShown] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const lan = !["127.0.0.1", "localhost", "::1"].includes(s.host);

  async function act(action: "generate" | "rotate" | "clear") {
    setError(null);
    try {
      const r = await apiKey(action);
      setShown(r.key);
      setCopied(false);
      onChange(r);
    } catch (e) {
      setError((e as ApiError).message);
    }
  }

  return (
    <Section title="Access">
      <FileOnly label="Listening on" value={`${s.host}:${s.port}`} command="strixrun config set host 0.0.0.0" />
      <Row label="API key" hint={lan ? "Required: StrixRun is reachable from other devices" : "Optional while StrixRun only listens on this computer"}>
        <span className="key-actions">
          <span className="mono settings-value">{s.api_key_set ? "set" : "not set"}</span>
          {s.api_key_set ? (
            <>
              <button type="button" className="btn btn-sm btn-secondary pressable" onClick={() => act("rotate")}>Replace key</button>
              {!lan && <button type="button" className="btn btn-sm btn-ghost pressable" onClick={() => act("clear")}>Remove</button>}
            </>
          ) : (
            <button type="button" className="btn btn-sm btn-secondary pressable" onClick={() => act("generate")}>Create key</button>
          )}
        </span>
      </Row>
      {shown && (
        <div className="key-reveal" role="status">
          <p>Copy this key now. It won't be shown again. Clients send it as <code className="mono">Authorization: Bearer …</code></p>
          <div className="key-box">
            <code className="mono">{shown}</code>
            <button type="button" className="btn btn-sm btn-secondary pressable" onClick={async () => {
              await navigator.clipboard?.writeText(shown);
              setCopied(true);
            }}>
              {copied ? <><Check /> Copied</> : <><Copy /> Copy</>}
            </button>
          </div>
        </div>
      )}
      {error && <p className="form-error" role="alert">{error}</p>}
      {s.api_key_set && (
        <Toggle label="Don't ask for the key on this computer" checked={s.trust_loopback} onChange={(v) => onPatch({ trust_loopback: v })} />
      )}
    </Section>
  );
}

function HfToken({ s, onChange }: { s: Settings; onChange: () => void }) {
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  async function save(value: string | null) {
    setError(null);
    try {
      await setHfToken(value);
      setToken("");
      onChange();
    } catch (e) {
      setError((e as ApiError).message);
    }
  }
  const status = s.hf_token.set ? (s.hf_token.source === "env" ? "set by HF_TOKEN" : "set") : "not set";
  return (
    <Section title="Hugging Face" note="A token raises download rate limits and unlocks gated models whose license you've accepted.">
      <Row label="Access token" hint={<>Create one at <a className="link-button" href="https://huggingface.co/settings/tokens" target="_blank" rel="noreferrer noopener">huggingface.co/settings/tokens</a></>}>
        <span className="mono settings-value">{status}</span>
      </Row>
      {s.hf_token.source !== "env" && (
        <form className="token-row" onSubmit={(e) => { e.preventDefault(); if (token.trim()) save(token.trim()); }}>
          <label className="sr-only" htmlFor="hf-token">Hugging Face token</label>
          <input id="hf-token" className="text-field text-field-mono" type="password" autoComplete="off" placeholder="hf_…"
            value={token} onChange={(e) => setToken(e.target.value)} />
          <button type="submit" className="btn btn-md btn-secondary pressable" disabled={!token.trim()}>Save token</button>
          {s.hf_token.set && <button type="button" className="btn btn-md btn-ghost pressable" onClick={() => save(null)}>Remove</button>}
        </form>
      )}
      {error && <p className="form-error" role="alert">{error}</p>}
    </Section>
  );
}

type Progress = { phase: string; asset?: string; done?: number; total?: number; message?: string };

function Binary() {
  const [b, setB] = useState<BinaryStatus | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(() => getBinary().then(setB).catch((e: ApiError) => setError(e.message)), []);
  useEffect(() => {
    refresh();
    return subscribe("/admin/binary/events", {
      "binary.progress": (d) => {
        const p = d as Progress;
        setProgress(p);
        if (p.phase === "done" || p.phase === "error") refresh();
      },
    });
  }, [refresh]);

  async function run(fn: () => Promise<BinaryStatus>) {
    setError(null);
    try {
      setB(await fn());
    } catch (e) {
      setError((e as ApiError).message);
    }
  }

  if (!b) return null;
  const upgradable = b.current_tag !== b.default_tag;
  const busy = Boolean(b.installing);
  return (
    <Section title="llama.cpp">
      <Row label="Installed build" hint={b.override_path ? "Using your own llama-server (binary_path)" : `For ${b.variant ?? "this platform"}`}>
        <span className="mono settings-value">{b.override_path ?? b.current_tag ?? "not installed"}</span>
      </Row>
      {!b.override_path && (
        <Row label="Pinned build" hint="The version this StrixRun release was tested with">
          <span className="key-actions">
            <span className="mono settings-value">{b.default_tag}</span>
            {(upgradable || !b.ready) && (
              <button type="button" className="btn btn-sm btn-secondary pressable" disabled={busy} onClick={() => run(() => installBinary(b.default_tag))}>
                {b.ready ? "Switch to pinned" : "Install"}
              </button>
            )}
            {b.previous_tag && (
              <button type="button" className="btn btn-sm btn-ghost pressable" disabled={busy} onClick={() => run(rollbackBinary)}>
                Roll back to {b.previous_tag}
              </button>
            )}
          </span>
        </Row>
      )}
      {busy && progress && (
        <div className="binary-progress" aria-live="polite">
          <span className="mono meta">
            {progress.phase === "download" && progress.total
              ? `Downloading ${progress.asset} · ${fmtBytes(progress.done)} / ${fmtBytes(progress.total)}`
              : { verify: "Verifying checksum", extract: "Extracting", smoke_test: "Checking it runs", done: "Installed ✓" }[progress.phase] ?? progress.phase}
          </span>
          {progress.phase === "download" && progress.total ? (
            <div className="progress"><span style={{ width: `${(100 * (progress.done ?? 0)) / progress.total}%` }} /></div>
          ) : null}
        </div>
      )}
      {(error || b.last_error) && <p className="form-error" role="alert">{error ?? b.last_error}</p>}
    </Section>
  );
}

const LIMIT_TEXT = {
  vram: "free VRAM first; what doesn't fit runs from system RAM on the CPU",
  "vram+ram": "free VRAM plus system RAM",
  metal: "the Metal GPU limit (shared memory)",
  ram: "available system RAM (no NVIDIA GPU found)",
} as const;

function Machine() {
  const [info, setInfo] = useState<SystemInfo | null>(null);
  useEffect(() => {
    getSystem().then(setInfo).catch(() => {});
  }, []);
  if (!info) return null;
  return (
    <Section title="This machine" note="What StrixRun detects. Memory estimates use these numbers.">
      <Row label="System RAM">
        <span className="mono settings-value">{fmtBytes(info.ram_available_bytes)} free of {fmtBytes(info.ram_total_bytes)}</span>
      </Row>
      {info.gpus.length === 0 && !info.apple_silicon && (
        <Row label="GPU" hint="nvidia-smi wasn't found or didn't answer; models run on the CPU">
          <span className="mono settings-value">none detected</span>
        </Row>
      )}
      {info.gpus.map((g) => (
        <Row key={g.index} label={info.gpus.length > 1 ? `GPU ${g.index}: ${g.name}` : g.name}>
          <span className="mono settings-value">{fmtBytes(g.free_bytes)} free of {fmtBytes(g.total_bytes)}</span>
        </Row>
      ))}
      {info.apple_silicon && info.metal_limit_bytes != null && (
        <Row label="Apple GPU" hint="Shares system memory, up to the Metal working-set limit">
          <span className="mono settings-value">{fmtBytes(info.metal_limit_bytes)} usable now</span>
        </Row>
      )}
      <Row label="Estimates compare against">
        <span className="settings-value">{LIMIT_TEXT[info.default_limit]}</span>
      </Row>
    </Section>
  );
}
