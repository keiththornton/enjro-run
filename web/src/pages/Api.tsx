import { Check, Copy } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { listModels } from "../api/models";
import { getSettings } from "../api/settings";
import { type Feature, type Lang, appGuides, snippet } from "../api/snippets";
import type { ModelView, Settings } from "../api/types";

const LANGS: { value: Lang; label: string }[] = [
  { value: "curl", label: "curl" },
  { value: "python", label: "Python" },
  { value: "javascript", label: "JavaScript" },
];
const FEATURES: { value: Feature; label: string }[] = [
  { value: "chat", label: "Chat" },
  { value: "stream", label: "Streaming" },
  { value: "tools", label: "Tool calls" },
  { value: "json", label: "JSON output" },
  { value: "embeddings", label: "Embeddings" },
];
const INSTALL = { python: "pip install openai", javascript: "npm install openai" } as const;

const ENDPOINTS = [
  ["POST", "/v1/chat/completions", "Chat. Supports stream, tools / tool_choice, response_format (json_object, json_schema), temperature, top_p, max_tokens, seed."],
  ["POST", "/v1/completions", "Plain text completion from a prompt."],
  ["POST", "/v1/embeddings", "Embedding vectors. Needs a model loaded in embedding mode."],
  ["GET", "/v1/models", "Loaded models, one entry per name. Add ?all=1 to include downloaded models that aren't loaded."],
  ["GET", "/upstream/{model}/{health|props|slots}", "Read-only llama-server details for a loaded model. POST /tokenize and /detokenize also work."],
  ["GET", "/metrics", "Prometheus metrics: requests, time to first token, tokens per second, queue depth."],
] as const;

const ERRORS = [
  ["401", "invalid_api_key", "The key is missing or wrong.", "Send Authorization: Bearer <key>."],
  ["404", "model_not_found", "No model has that name.", "Use a name from GET /v1/models?all=1."],
  ["409", "model_not_loaded", "The model is downloaded but not loaded.", "Load it on the Models page or run strixrun load <name>."],
  ["409", "model_crashed", "The model stopped unexpectedly.", "See the reason on the Models page, then load it again."],
  ["429", "queue_full", "Every slot is busy and the queue is full.", "Retry shortly, or load the model with more parallel requests."],
  ["502", "upstream_error", "llama-server failed or stopped responding.", "Check the model's logs on the Models page."],
  ["503", "model_loading", "The model is still loading.", "Retry after the Retry-After header (a few seconds)."],
] as const;

export function ApiPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [models, setModels] = useState<ModelView[]>([]);
  const [model, setModel] = useState("");
  const [lang, setLang] = useState<Lang>("python");
  const [feature, setFeature] = useState<Feature>("chat");

  useEffect(() => {
    getSettings().then(setSettings).catch(() => {});
    listModels().then((m) => {
      setModels(m);
      const chat = m.find((x) => x.state.status === "ready" && x.preset.mode === "chat") ?? m.find((x) => x.preset.mode === "chat");
      setModel((cur) => cur || chat?.aliases[0] || "");
    }).catch(() => {});
  }, []);

  const loaded = models.filter((m) => m.state.status === "ready");
  const chatNames = loaded.filter((m) => m.preset.mode === "chat").flatMap((m) => m.aliases);
  const embedding = loaded.find((m) => m.preset.mode === "embedding") ?? models.find((m) => m.preset.mode === "embedding");
  const lan = settings ? !["127.0.0.1", "localhost", "::1"].includes(settings.host) : false;
  const keyRequired = Boolean(settings?.api_key_set && (lan || !settings.trust_loopback || !isLoopback()));
  const ctx = useMemo(() => ({
    baseUrl: `${window.location.origin}/v1`,
    model: model || "<model-name>",
    embeddingModel: embedding?.aliases[0] ?? "<embedding-model>",
    keyRequired,
  }), [model, embedding, keyRequired]);

  return (
    <section className="page-body api-page">
      <h1 className="page-title">Connect apps</h1>
      <p className="page-sub">
        StrixRun speaks the OpenAI API, so anything that lets you set a base URL works: SDKs, Open WebUI, editors, agents.
      </p>

      <section className="settings-section" aria-label="Connection">
        <h2 className="eyebrow">Connection</h2>
        <div className="settings-rows">
          <div className="settings-row">
            <div className="settings-label"><span>Base URL</span><span className="field-hint">Use this wherever an app asks for an OpenAI base URL</span></div>
            <CopyValue value={ctx.baseUrl} />
          </div>
          <div className="settings-row">
            <div className="settings-label">
              <span>API key</span>
              <span className="field-hint">
                {keyRequired
                  ? "Required here. Send it as Authorization: Bearer <key>; the examples read it from STRIXRUN_API_KEY."
                  : "Not needed from this computer. Apps that insist on a key can use any text."}
              </span>
            </div>
            <a className="btn btn-sm btn-secondary pressable" href="#/settings">{settings?.api_key_set ? "Manage key" : "Create a key"}</a>
          </div>
          {!lan && (
            <div className="settings-row">
              <div className="settings-label">
                <span>Other devices</span>
                <span className="field-hint">
                  Only apps on this computer can connect. To share on your network, run <code className="mono">strixrun key set</code>, then{" "}
                  <code className="mono">strixrun config set host 0.0.0.0</code>, and restart StrixRun.
                </span>
              </div>
            </div>
          )}
        </div>
      </section>

      <section className="settings-section" aria-label="Model names">
        <h2 className="eyebrow">Model names</h2>
        <p className="settings-note">
          Put one of these in the <code className="mono">model</code> field. Add names such as <code className="mono">gpt-4o-mini</code> on the Models page
          (pencil icon) so apps with a hard-coded model work unchanged.
        </p>
        {loaded.length === 0 ? (
          <p className="empty">Nothing is loaded yet. <a className="link-button" href="#/models">Load a model</a> first; requests for an unloaded model get a 409.</p>
        ) : (
          <ul className="name-list">
            {loaded.map((m) => (
              <li key={m.id}>
                {m.aliases.map((a) => <CopyChip key={a} value={a} />)}
                <span className="meta mono">{m.preset.mode}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="settings-section" aria-label="Code examples">
        <div className="examples-head">
          <h2 className="eyebrow">Code</h2>
          {chatNames.length > 1 && (
            <label className="examples-model">
              <span className="meta">Model</span>
              <select className="text-field text-field-mono" value={model} onChange={(e) => setModel(e.target.value)}>
                {chatNames.map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
          )}
        </div>
        <div className="examples-tabs">
          <div className="segmented" role="group" aria-label="Language">
            {LANGS.map((l) => <button key={l.value} type="button" aria-pressed={lang === l.value} onClick={() => setLang(l.value)}>{l.label}</button>)}
          </div>
          <div className="segmented" role="group" aria-label="Example">
            {FEATURES.map((f) => <button key={f.value} type="button" aria-pressed={feature === f.value} onClick={() => setFeature(f.value)}>{f.label}</button>)}
          </div>
        </div>
        {lang !== "curl" && <p className="settings-note mono install-line">{INSTALL[lang]}</p>}
        <CodeBlock code={snippet(lang, feature, ctx)} label={`${LANGS.find((l) => l.value === lang)!.label} example: ${feature}`} />
      </section>

      <section className="settings-section" aria-label="Apps">
        <h2 className="eyebrow">Apps</h2>
        <div className="guides">
          {appGuides(ctx).map((g) => (
            <article key={g.name} className="guide">
              <h3 className="guide-title">{g.name}</h3>
              <ol className="guide-steps">{g.steps.map((s) => <li key={s}>{s}</li>)}</ol>
              {g.code && <CodeBlock code={g.code} label={g.codeLabel ?? g.name} />}
            </article>
          ))}
        </div>
      </section>

      <section className="settings-section" aria-label="Endpoints">
        <h2 className="eyebrow">Endpoints</h2>
        <div className="ref-table">
          <table>
            <tbody>
              {ENDPOINTS.map(([method, path, what]) => (
                <tr key={path}>
                  <td className="mono ref-method">{method}</td>
                  <td className="mono ref-path">{path}</td>
                  <td>{what}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="settings-section" aria-label="Errors">
        <h2 className="eyebrow">Errors</h2>
        <p className="settings-note">Errors use OpenAI's shape, <code className="mono">{'{"error": {"message", "type", "code"}}'}</code>, so SDKs raise their usual exceptions.</p>
        <div className="ref-table">
          <table>
            <thead><tr><th scope="col">Status</th><th scope="col">Code</th><th scope="col">Meaning</th><th scope="col">What to do</th></tr></thead>
            <tbody>
              {ERRORS.map(([status, code, meaning, fix]) => (
                <tr key={code}>
                  <td className="mono">{status}</td>
                  <td className="mono">{code}</td>
                  <td>{meaning}</td>
                  <td>{fix}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </section>
  );
}

function isLoopback() {
  return ["127.0.0.1", "localhost", "[::1]"].includes(window.location.hostname);
}

function useCopy(): [boolean, (text: string) => void] {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(t);
  }, [copied]);
  return [copied, (text) => {
    navigator.clipboard?.writeText(text).then(() => setCopied(true)).catch(() => {});
  }];
}

function CopyValue({ value }: { value: string }) {
  const [copied, copy] = useCopy();
  return (
    <span className="copy-value">
      <code className="mono settings-value">{value}</code>
      <button type="button" className="icon-btn" aria-label={copied ? "Copied" : `Copy ${value}`} onClick={() => copy(value)}>
        {copied ? <Check /> : <Copy />}
      </button>
    </span>
  );
}

function CopyChip({ value }: { value: string }) {
  const [copied, copy] = useCopy();
  return (
    <button type="button" className="chip copy-chip" onClick={() => copy(value)} aria-label={`Copy model name ${value}`}>
      {value} {copied ? <Check /> : <Copy />}
    </button>
  );
}

function CodeBlock({ code, label }: { code: string; label: string }) {
  const [copied, copy] = useCopy();
  return (
    <div className="code-wrap">
      <button type="button" className="btn btn-sm btn-secondary pressable code-copy" onClick={() => copy(code)} aria-label={`Copy ${label}`}>
        {copied ? <><Check /> Copied</> : <><Copy /> Copy</>}
      </button>
      <pre className="code-block code-example" aria-label={label}><code>{code}</code></pre>
    </div>
  );
}
