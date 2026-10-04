import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { type ReactNode, useEffect, useId, useMemo, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { fmtBytes, fmtCtx } from "../api/format";
import { estimate, fitModel, loadModel } from "../api/models";
import type { Estimate, ModelView, Preset } from "../api/types";

const CTX_STEPS = [2048, 4096, 8192, 16384, 32768, 65536, 131072, 262144, 524288, 1048576];
const CACHE_TYPES = [
  { value: "f16", label: "f16" },
  { value: "q8_0", label: "q8_0" },
  { value: "q4_0", label: "q4_0" },
];

type Props = {
  model: ModelView | null;
  logs: string[];
  onClose: () => void;
};

/** Side sheet: preset form, live memory estimate, then live load progress. */
export function LoadDrawer({ model, logs, onClose }: Props) {
  return (
    <Dialog.Root open={model !== null} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="sheet" aria-describedby={undefined}>
          {model && <LoadForm key={model.id} model={model} logs={logs} onClose={onClose} />}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function LoadForm({ model, logs, onClose }: { model: ModelView; logs: string[]; onClose: () => void }) {
  const [preset, setPreset] = useState<Preset>(model.preset);
  const [extra, setExtra] = useState(model.preset.extra_args.join(" "));
  const [est, setEst] = useState<Estimate | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const status = model.state.status;
  const trained = model.meta.context_length ?? 131072;
  const ctxOptions = useMemo(() => {
    const steps = CTX_STEPS.filter((c) => c <= trained);
    return steps.includes(preset.ctx_per_request) ? steps : [...steps, preset.ctx_per_request].sort((a, b) => a - b);
  }, [trained, preset.ctx_per_request]);

  const body = useMemo(
    () => ({ ...preset, extra_args: extra.trim() ? extra.trim().split(/\s+/) : [] }),
    [preset, extra],
  );

  // Live estimate, debounced while the form changes.
  useEffect(() => {
    const t = setTimeout(() => {
      estimate(model.id, body)
        .then((e) => {
          setEst(e);
          setError(null);
        })
        .catch((e: ApiError) => setError(e.message));
    }, 200);
    return () => clearTimeout(t);
  }, [model.id, body]);

  // Close shortly after the model becomes ready. The latest onClose lives in a ref so
  // re-renders (every streamed log line) don't keep restarting the timer.
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    if (submitted && status === "ready") {
      const t = setTimeout(() => closeRef.current(), 600);
      return () => clearTimeout(t);
    }
  }, [submitted, status]);

  const needsConfirm = est && est.verdict !== "ok";
  const [fitNote, setFitNote] = useState<string | null>(null);

  async function fitOnGpu() {
    setError(null);
    try {
      const r = await fitModel(model.id, body);
      setFitNote(r.message);
      if (r.fits) {
        setPreset((p) => ({ ...p, n_gpu_layers: r.preset.n_gpu_layers, ctx_per_request: r.preset.ctx_per_request,
                           cache_type_k: r.preset.cache_type_k, cache_type_v: r.preset.cache_type_v,
                           flash_attn: r.preset.flash_attn }));
      }
    } catch (e) {
      setError((e as ApiError).message);
    }
  }
  const busy = submitted && (status === "loading" || status === "stopping");

  async function submit() {
    setError(null);
    try {
      await loadModel(model.id, body, Boolean(needsConfirm));
      setSubmitted(true);
    } catch (e) {
      setError((e as ApiError).message);
    }
  }

  const set = <K extends keyof Preset>(k: K, v: Preset[K]) => setPreset((p) => ({ ...p, [k]: v }));

  return (
    <div className="sheet-inner">
      <header className="sheet-header">
        <div className="sheet-heading">
          <Dialog.Title className="dialog-title">Load {model.aliases[0]}</Dialog.Title>
          <p className="sheet-sub mono">{[model.repo ?? model.id, model.quant, fmtBytes(model.size_bytes)].filter(Boolean).join(" · ")}</p>
        </div>
        <Dialog.Close className="icon-btn" aria-label="Close">
          <X />
        </Dialog.Close>
      </header>

      <div className="sheet-body">
        {submitted ? (
          <LoadProgress model={model} logs={logs} />
        ) : (
          <>
            <div className="form-grid">
              <Field label="Context per request" hint={`Trained for ${fmtCtx(trained)} tokens`}>
                {(f) => (
                <select {...f} className="text-field" value={preset.ctx_per_request} onChange={(e) => set("ctx_per_request", Number(e.target.value))}>
                  {ctxOptions.map((c) => (
                    <option key={c} value={c}>{fmtCtx(c)} tokens</option>
                  ))}
                </select>
                )}
              </Field>
              <Field label="Parallel requests" hint="Served at once; they share one context, so this doesn't add memory">
                {(f) => (
                <input {...f} className="text-field text-field-mono" type="number" min={1} max={16} value={preset.parallel}
                  onChange={(e) => set("parallel", Math.max(1, Number(e.target.value) || 1))} />
                )}
              </Field>
              <Field label="GPU layers" hint={GPU_HINT[gpuMode(preset.n_gpu_layers)]}>
                {(f) => (
                <span className="gpu-layers">
                  <select {...f} className="text-field" value={gpuMode(preset.n_gpu_layers)}
                    onChange={(e) => set("n_gpu_layers", { auto: -1, all: 999, cpu: 0, custom: 20 }[e.target.value as GpuMode])}>
                    <option value="auto">Auto (recommended)</option>
                    <option value="all">All on GPU</option>
                    <option value="cpu">CPU only</option>
                    <option value="custom">Custom…</option>
                  </select>
                  {gpuMode(preset.n_gpu_layers) === "custom" && (
                    <input className="text-field text-field-mono" type="number" min={1} max={998} aria-label="Number of GPU layers"
                      value={preset.n_gpu_layers} onChange={(e) => set("n_gpu_layers", Math.min(998, Math.max(1, Number(e.target.value) || 1)))} />
                  )}
                </span>
                )}
              </Field>
              <Field label="Flash attention">
                {(f) => (
                <Segmented {...f} value={preset.flash_attn} options={["auto", "on", "off"]} onChange={(v) => set("flash_attn", v as Preset["flash_attn"])} />
                )}
              </Field>
              <Field label="KV cache" hint="Quantized caches need flash attention">
                {(f) => (
                <Segmented
                  {...f}
                  value={preset.cache_type_k}
                  options={CACHE_TYPES.map((c) => c.value)}
                  onChange={(v) => setPreset((p) => ({ ...p, cache_type_k: v, cache_type_v: v }))}
                />
                )}
              </Field>
              <Field label="Mode">
                {(f) => (
                <Segmented {...f} value={preset.mode} options={["chat", "embedding"]} onChange={(v) => set("mode", v as Preset["mode"])} />
                )}
              </Field>
              <Field label="Extra arguments" hint="Performance flags only, e.g. --threads 8" wide>
                {(f) => (
                <input {...f} className="text-field text-field-mono" value={extra} spellCheck={false} placeholder="--threads 8"
                  onChange={(e) => setExtra(e.target.value)} />
                )}
              </Field>
            </div>
            <EstimateView est={est} />
            {est?.placement === "split" && (
              <div className="fit-banner" role="status">
                <p>Part of this model would run on the CPU, which slows every reply, even in a new conversation.
                  Fit on GPU finds the most context that keeps every layer on the GPU.</p>
                <button type="button" className="btn btn-sm btn-secondary pressable" onClick={fitOnGpu}>Fit on GPU</button>
              </div>
            )}
            {fitNote && <p className="field-hint fit-note">{fitNote}</p>}
          </>
        )}
        {error && <p className="form-error" role="alert">{error}</p>}
      </div>

      <footer className="sheet-footer">
        <Dialog.Close className="btn btn-md btn-ghost pressable">{submitted ? "Close" : "Cancel"}</Dialog.Close>
        {!submitted && (
          <button type="button" className="btn btn-md btn-primary pressable" onClick={submit} disabled={!est || busy}>
            {needsConfirm ? "Load anyway" : "Load"}
          </button>
        )}
      </footer>
    </div>
  );
}

type FieldProps = { id: string; "aria-describedby"?: string };

function Field({ label, hint, wide, children }: {
  label: string; hint?: string; wide?: boolean; children: (props: FieldProps) => ReactNode;
}) {
  const id = useId();
  const hintId = `${id}-hint`;
  return (
    <div className={`field${wide ? " field-wide" : ""}`}>
      <label className="field-label" htmlFor={id} id={`${id}-label`}>{label}</label>
      {children({ id, "aria-describedby": hint ? hintId : undefined })}
      {hint && <span className="field-hint" id={hintId}>{hint}</span>}
    </div>
  );
}

function Segmented({ value, options, onChange, ...field }: FieldProps & {
  value: string; options: string[]; onChange: (v: string) => void;
}) {
  return (
    <div className="segmented segmented-field" role="group" id={field.id} aria-labelledby={`${field.id}-label`}
      aria-describedby={field["aria-describedby"]}>
      {options.map((o) => (
        <button key={o} type="button" aria-pressed={value === o} onClick={() => onChange(o)}>{o}</button>
      ))}
    </div>
  );
}

type GpuMode = "auto" | "all" | "cpu" | "custom";
const gpuMode = (n: number): GpuMode => (n === -1 ? "auto" : n === 0 ? "cpu" : n >= 999 ? "all" : "custom");
const GPU_HINT: Record<GpuMode, string> = {
  auto: "llama.cpp puts what fits on the GPU and runs the rest on the CPU",
  all: "Every layer on the GPU; fails if it doesn't fit",
  cpu: "No GPU: slowest, uses system RAM only",
  custom: "This many layers on the GPU, the rest on the CPU",
};

const LIMIT_LABEL = {
  ram: "available memory",
  metal: "the Metal GPU limit",
  vram: "free VRAM",
  "vram+ram": "free VRAM plus available system RAM",
} as const;

export function EstimateView({ est }: { est: Estimate | null }) {
  if (!est) return <div className="estimate estimate-pending" aria-busy="true">Estimating memory…</div>;
  const total = Math.max(est.estimate_bytes, est.available_bytes, 1);
  const pct = (n: number) => `${(100 * n) / total}%`;
  const headline =
    est.verdict === "ok"
      ? est.placement === "split" ? "Fits, partly on the CPU (slower)" : est.placement === "gpu" && est.limit === "vram" ? "Fits on the GPU" : "Fits"
      : est.verdict === "tight" ? "Tight: loading may slow this machine down" : "Likely too large for this machine";
  return (
    <div className={`estimate estimate-${est.verdict}`}>
      <div className="estimate-head">
        <span className="estimate-verdict">{headline}</span>
        <span className="mono estimate-numbers">
          {fmtBytes(est.estimate_bytes)} of {fmtBytes(est.available_bytes)}
        </span>
      </div>
      <div className="estimate-bar" role="img"
        aria-label={`Weights ${fmtBytes(est.weights_bytes)}, KV cache ${fmtBytes(est.kv_bytes)}, buffers ${fmtBytes(est.compute_bytes)}; ${fmtBytes(est.available_bytes)} available`}>
        <span className="seg seg-weights" style={{ width: pct(est.weights_bytes) }} />
        <span className="seg seg-kv" style={{ width: pct(est.kv_bytes) }} />
        <span className="seg seg-compute" style={{ width: pct(est.compute_bytes) }} />
        <span className="estimate-limit" style={{ left: pct(est.available_bytes) }} />
      </div>
      <dl className="estimate-legend mono">
        <div><dt><i className="swatch seg-weights" />weights</dt><dd>{fmtBytes(est.weights_bytes)}</dd></div>
        <div><dt><i className="swatch seg-kv" />kv cache</dt><dd>{fmtBytes(est.kv_bytes)}</dd></div>
        <div><dt><i className="swatch seg-compute" />buffers</dt><dd>{fmtBytes(est.compute_bytes)}</dd></div>
        <div><dt><i className="swatch swatch-limit" />available</dt><dd>{fmtBytes(est.available_bytes)}</dd></div>
      </dl>
      <p className="estimate-note">
        Estimate against {LIMIT_LABEL[est.limit]}
        {est.vram_free_bytes != null && est.limit !== "vram" ? ` (${fmtBytes(est.vram_free_bytes)} VRAM free)` : ""}.
        {est.notes.length ? ` ${est.notes.join(". ")}.` : ""}
      </p>
    </div>
  );
}

function LoadProgress({ model, logs }: { model: ModelView; logs: string[] }) {
  const tail = useRef<HTMLPreElement>(null);
  useEffect(() => {
    tail.current?.scrollTo({ top: tail.current.scrollHeight });
  }, [logs.length]);
  const { status, reason } = model.state;
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (status !== "loading") return;
    const start = Date.now();
    const t = setInterval(() => setElapsed((Date.now() - start) / 1000), 100);
    return () => clearInterval(t);
  }, [status]);
  return (
    <div className="load-progress" aria-live="polite">
      <div className="load-status">
        {status === "loading" && <><span className="spinner" aria-hidden /> <span>Loading</span> <span className="mono meta">{elapsed.toFixed(1)}s</span></>}
        {status === "ready" && <span>Ready ✓</span>}
        {status === "crashed" && <span className="danger">Couldn't load: {reason}</span>}
        {status === "stopped" && <span className="meta">Stopped</span>}
      </div>
      <pre className="log-tail" ref={tail} tabIndex={0} aria-label="llama-server output">
        {logs.slice(-200).join("\n") || "Waiting for llama-server…"}
      </pre>
    </div>
  );
}
