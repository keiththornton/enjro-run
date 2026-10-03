import { ArrowUp, RotateCcw, Square } from "lucide-react";
import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";
import { streamChat } from "../api/chat";
import type { ApiError } from "../api/client";
import { fmtMs, fmtRate } from "../api/format";
import { listModels } from "../api/models";
import type { ModelView } from "../api/types";
import { type ChatMessage, takeReplay } from "../app/replay";

type Turn = ChatMessage & {
  reasoning?: string;
  stats?: { ttft: number | null; tps: number | null; tokens: number | null };
  error?: string;
  streaming?: boolean;
};

type Params = { temperature: number; top_p: number; max_tokens: number; seed: string };
const DEFAULTS: Params = { temperature: 0.7, top_p: 0.95, max_tokens: 512, seed: "" };

export function PlaygroundPage() {
  const [models, setModels] = useState<ModelView[] | null>(null);
  const [model, setModel] = useState("");
  const [system, setSystem] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [params, setParams] = useState<Params>(DEFAULTS);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const log = useRef<HTMLDivElement>(null);
  const streaming = turns.some((t) => t.streaming);

  useEffect(() => {
    listModels()
      .then((all) => {
        const chat = all.filter((m) => m.state.status === "ready" && m.preset.mode === "chat");
        setModels(chat);
        const replay = takeReplay();
        if (replay) {
          const sys = replay.messages.find((m) => m.role === "system");
          setSystem(sys?.content ?? "");
          setTurns(replay.messages.filter((m) => m.role !== "system"));
          setParams((p) => ({
            ...p,
            ...("temperature" in replay.params ? { temperature: replay.params.temperature } : {}),
            ...("top_p" in replay.params ? { top_p: replay.params.top_p } : {}),
            ...("max_tokens" in replay.params ? { max_tokens: replay.params.max_tokens } : {}),
            ...("seed" in replay.params ? { seed: String(replay.params.seed) } : {}),
          }));
          const match = chat.find((m) => m.aliases.includes(replay.model) || m.id === replay.model);
          setModel(match ? match.aliases[0] : chat[0]?.aliases[0] ?? "");
        } else {
          setModel((cur) => cur || chat[0]?.aliases[0] || "");
        }
      })
      .catch((e: ApiError) => setError(e.message));
    return () => abort.current?.abort();
  }, []);

  useEffect(() => {
    log.current?.scrollTo({ top: log.current.scrollHeight });
  }, [turns]);

  async function run(history: Turn[]) {
    setError(null);
    const controller = new AbortController();
    abort.current = controller;
    const started = performance.now();
    let first: number | null = null;
    const reply: Turn = { role: "assistant", content: "", streaming: true };
    setTurns([...history, reply]);
    const update = (patch: Partial<Turn>) => {
      Object.assign(reply, patch);
      setTurns([...history, { ...reply }]);
    };
    const messages = [...(system.trim() ? [{ role: "system", content: system }] : []),
      ...history.map(({ role, content }) => ({ role, content }))];
    const body: Record<string, unknown> = {
      model, messages, temperature: params.temperature, top_p: params.top_p, max_tokens: params.max_tokens,
    };
    if (params.seed.trim()) body.seed = Number(params.seed);
    try {
      for await (const d of streamChat(body, controller.signal)) {
        if (d.error) update({ error: d.error });
        if (d.content || d.reasoning) {
          first ??= performance.now() - started;
          update({
            content: reply.content + (d.content ?? ""),
            reasoning: d.reasoning ? (reply.reasoning ?? "") + d.reasoning : reply.reasoning,
          });
        }
        if (d.usage || d.timings) {
          update({ stats: { ttft: first, tps: d.timings?.predicted_per_second ?? null, tokens: d.usage?.completion_tokens ?? null } });
        }
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") update({ error: (e as ApiError).message });
    } finally {
      update({ streaming: false, stats: reply.stats ?? { ttft: first, tps: null, tokens: null } });
      abort.current = null;
    }
  }

  function send(e?: FormEvent) {
    e?.preventDefault();
    if (!draft.trim() || streaming || !model) return;
    const history = [...turns.filter((t) => !t.streaming), { role: "user" as const, content: draft.trim() }];
    setDraft("");
    run(history);
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  }

  const lastIsUser = turns.length > 0 && turns[turns.length - 1].role === "user";

  if (models !== null && models.length === 0) {
    return (
      <section className="page-body">
        <h1 className="page-title">Playground</h1>
        <p className="empty">No chat model is loaded. <a className="link-button" href="#/models">Load one on the Models page</a> to try it here.</p>
      </section>
    );
  }

  return (
    <section className="playground">
      <div className="pg-main">
        <div className="pg-toolbar">
          <label className="sr-only" htmlFor="pg-model">Model</label>
          <select id="pg-model" className="text-field text-field-mono pg-model" value={model} onChange={(e) => setModel(e.target.value)}>
            {models?.map((m) => <option key={m.id} value={m.aliases[0]}>{m.aliases[0]}</option>)}
          </select>
          <button type="button" className="btn btn-sm btn-ghost pressable" onClick={() => setTurns([])} disabled={streaming || turns.length === 0}>
            <RotateCcw /> New chat
          </button>
        </div>

        <div className="pg-log" ref={log} aria-live="polite">
          {turns.length === 0 && <p className="meta pg-hint">Send a message to {model || "the model"}. Shift+Enter adds a line.</p>}
          {turns.map((t, i) => (
            <article key={i} className={`turn turn-${t.role}`}>
              {t.reasoning && <p className="turn-reasoning">{t.reasoning}</p>}
              <p className="turn-text">{t.content || (t.streaming ? "" : "—")}</p>
              {t.streaming && !t.content && <span className="meta mono turn-waiting">waiting on {model}</span>}
              {t.error && <p className="form-error">{t.error}</p>}
              {t.stats && !t.streaming && (
                <p className="turn-stats mono">
                  ttft {fmtMs(t.stats.ttft)} · {fmtRate(t.stats.tps)} tok/s{t.stats.tokens != null ? ` · ${t.stats.tokens} tokens` : ""}
                </p>
              )}
            </article>
          ))}
        </div>
        {error && <p className="form-error" role="alert">{error}</p>}

        <form className="composer" onSubmit={send}>
          <label className="sr-only" htmlFor="pg-input">Message</label>
          <textarea id="pg-input" className="composer-input" rows={2} placeholder={lastIsUser ? "Add a message, or run the conversation as is" : "Message"}
            value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={onKey} />
          {streaming ? (
            <button type="button" className="btn btn-md btn-secondary pressable" onClick={() => abort.current?.abort()}>
              <Square /> Stop
            </button>
          ) : lastIsUser && !draft.trim() ? (
            <button type="button" className="btn btn-md btn-primary pressable" onClick={() => run(turns)} disabled={!model}>
              Run
            </button>
          ) : (
            <button type="submit" className="btn btn-md btn-primary pressable" disabled={!draft.trim() || !model} aria-label="Send">
              <ArrowUp />
            </button>
          )}
        </form>
      </div>

      <aside className="pg-side" aria-label="Settings for this chat">
        <h2 className="eyebrow">System prompt</h2>
        <label className="sr-only" htmlFor="pg-system">System prompt</label>
        <textarea id="pg-system" className="text-field pg-system" rows={4} value={system} onChange={(e) => setSystem(e.target.value)}
          placeholder="Optional instructions for the model" />
        <h2 className="eyebrow pg-params-label">Sampling</h2>
        <Num label="Temperature" value={params.temperature} step={0.05} min={0} max={2} onChange={(v) => setParams({ ...params, temperature: v })} />
        <Num label="Top p" value={params.top_p} step={0.01} min={0} max={1} onChange={(v) => setParams({ ...params, top_p: v })} />
        <Num label="Max tokens" value={params.max_tokens} step={1} min={1} max={32768} onChange={(v) => setParams({ ...params, max_tokens: Math.round(v) })} />
        <label className="pg-param">
          <span>Seed</span>
          <input className="text-field text-field-mono" inputMode="numeric" placeholder="random" value={params.seed}
            onChange={(e) => setParams({ ...params, seed: e.target.value.replace(/[^0-9]/g, "") })} />
        </label>
      </aside>
    </section>
  );
}

function Num({ label, value, step, min, max, onChange }: {
  label: string; value: number; step: number; min: number; max: number; onChange: (v: number) => void;
}) {
  return (
    <label className="pg-param">
      <span>{label}</span>
      <input className="text-field text-field-mono" type="number" value={value} step={step} min={min} max={max}
        onChange={(e) => onChange(Math.min(max, Math.max(min, Number(e.target.value) || 0)))} />
    </label>
  );
}
