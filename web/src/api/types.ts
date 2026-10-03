// Shapes returned by the StrixRun admin and /v1 APIs.

export type Status = "loading" | "ready" | "stopping" | "stopped" | "crashed";

export interface ModelState {
  status: Status;
  reason: string | null;
  crash_class: string | null;
  pid: number | null;
  port: number | null;
  started_at: number | null;
  ready_at: number | null;
}

export interface Preset {
  ctx_per_request: number;
  n_gpu_layers: number;
  parallel: number;
  queue_max: number;
  flash_attn: "on" | "off" | "auto";
  cache_type_k: string;
  cache_type_v: string;
  mode: "chat" | "embedding";
  chat_template: string | null;
  extra_args: string[];
  sampling: Record<string, number>;
}

export interface ModelView {
  id: string;
  aliases: string[];
  repo: string | null;
  quant: string | null;
  size_bytes: number;
  location: "store" | "scan";
  files: number;
  meta: { architecture?: string; context_length?: number; embedding_only?: boolean; parameters?: string; n_layer?: number };
  state: ModelState;
  preset: Preset;
  queue_depth: number;
}

export interface Estimate {
  estimate_bytes: number;
  weights_bytes: number;
  kv_bytes: number;
  compute_bytes: number;
  available_bytes: number;
  limit: "ram" | "metal" | "vram";
  verdict: "ok" | "tight" | "over";
  notes: string[];
  ctx_per_request?: number;
  parallel?: number;
}

export interface RequestRow {
  id: string;
  ts: number;
  model_id: string;
  alias_used: string | null;
  endpoint: "chat" | "completions" | "embeddings" | "upstream";
  stream: boolean;
  status: "ok" | "error" | "cancelled";
  http_status: number | null;
  error: string | null;
  client_ip: string | null;
  key_id: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  ttft_ms: number | null;
  total_ms: number | null;
  tok_per_s: number | null;
  queue_ms: number | null;
}

export interface RequestDetail extends RequestRow {
  bodies_available: boolean;
  request_body: unknown;
  response_body: unknown;
}

export interface HfSearchResult {
  repo: string;
  downloads: number;
  likes?: number;
  updated?: string | null;
  gated?: boolean;
}

export interface QuantRow {
  quant: string;
  files: string[];
  size_bytes: number;
  model_id: string;
  downloaded: boolean;
  fit: Estimate | null;
}

export interface RepoListing {
  repo: string;
  gated: boolean;
  meta: ModelView["meta"] | null;
  quants: QuantRow[];
}

export interface DownloadJob {
  id: string;
  repo: string;
  quant: string;
  files: string[];
  status: "queued" | "downloading" | "done" | "error" | "cancelled";
  done_bytes: number;
  total_bytes: number;
  error: string | null;
  model_id: string | null;
}

export interface Settings {
  host: string;
  port: number;
  api_key_set: boolean;
  trust_loopback: boolean;
  models_dirs: string[];
  extra_scan_dirs: string[];
  binary_tag: string | null;
  binary_variant: string | null;
  binary_path: string | null;
  log_body_ring_size: number;
  log_retention_days: number;
  restore_loaded: boolean;
  upstream_idle_timeout_s: number;
  file_only: string[];
  hf_token: { set: boolean; source: "env" | "store" | null };
}

export interface BinaryStatus {
  current_tag: string | null;
  previous_tag: string | null;
  variant: string | null;
  override_path: string | null;
  installed: string[];
  default_tag: string;
  available_tags: string[];
  installing: string | null;
  last_error: string | null;
  ready: boolean;
}

export interface PsEntry {
  id: string;
  aliases: string[];
  status: Status;
  pid: number | null;
  port: number | null;
  uptime_s: number | null;
  rss_bytes: number | null;
  ctx_per_request: number | null;
  parallel: number | null;
  queue_depth: number;
}
