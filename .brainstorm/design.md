# StrixRun — Design

Sister app to StrixCode and Strix Data. Status: approved design, revised after the multi-agent review, 2026-10-03.

## 1. Purpose and users

StrixRun is a local LLM inference server built around its API. It runs GGUF models through llama.cpp and serves an OpenAI-compatible API, with a light web dashboard alongside for managing models and watching traffic. It fills the gap between Ollama, which is API-first with only a chat GUI and no admin view, and LM Studio, which is GUI-first and closed source.

The primary user is the author, on an Apple M4 Mac (16 GB) and a Windows PC with an NVIDIA GPU. Secondary users are other devices and apps on the same LAN that call the API.

**Differentiators** (from the prior-art research):
1. **Request observability.** Every request records time to first token, tokens per second, prompt and completion tokens, the key used, its status and any error. The dashboard shows these live, and any recent request can be replayed in the playground.
2. **Memory-fit guidance before you download or load.** Hugging Face search shows a fit badge for each quantization, computed from remote GGUF headers. Loading shows an estimate and warns when memory is tight.
3. **Predictable residency.** Models are loaded and unloaded only by hand; nothing is swapped out unexpectedly. Requests for unknown or unloaded models get a clear error. Aliases let clients that expect OpenAI model names work unchanged. Models that were loaded are restored after a restart.
4. **Pinned, verified llama.cpp binaries** for each platform, with upgrade and rollback.

## 2. Success criteria (v1 is done when)

1. Real clients work unchanged: the OpenAI Python SDK, plus Open WebUI or Continue. Chat, streaming, tool calling, JSON-schema structured output and embeddings all work.
2. The full dashboard loop works from the dashboard alone: search Hugging Face, see fit badges, download, load with a preset and an estimate, chat in the playground, and see the request appear in the live log with its metrics.
3. LAN access works. Another device can call the API with the key, and when the dashboard is opened on that device it asks for the key and then works fully, including the live feeds.
4. It runs on macOS (Metal) and Windows (CUDA) on real hardware, and Linux (CPU) passes in CI.

## 3. Architecture

StrixRun is a single Python process (FastAPI on uvicorn) listening on one port. It serves the OpenAI API, the admin API, Prometheus metrics, a restricted upstream passthrough, and the static dashboard. Each loaded model is a separate `llama-server` child process bound to 127.0.0.1 on a private port. Each child has its own random `--api-key`, which only the proxy knows.

```
clients ─▶ ASGI security middleware (Host/Origin guard → auth)
            ├─ /v1/*                  OpenAI router ─▶ Proxy ─▶ Backend.target(id) ─▶ llama-server child
            ├─ /upstream/<model>/*    allowlisted passthrough ─▶ same child
            ├─ /admin/*               dashboard API (JSON + SSE), split by area
            ├─ /metrics               Prometheus text format
            └─ /                      React dashboard (static build; shell is public)
AppContext (built in lifespan): Settings · Registry · Presets · Backend · Proxy · RequestLog · Downloads · BinaryManager · EventBus
data: ~/.strix/run/{config.json, models.json, presets/, templates/, models/, bin/<tag>/, strixrun.db, run/*.pid, state.json}
```

**Process management.** StrixRun manages its own child processes rather than using llama.cpp's router mode. That is what allows per-model stderr capture, crash reasons, memory guardrails and pinned binaries.

**Backend interface.** Everything above the Supervisor talks only to the `Backend` protocol, so a router-mode backend could replace it later:
```python
class Backend(Protocol):
    async def load(self, model_id: str, preset: Preset) -> None
    async def unload(self, model_id: str) -> None
    def state(self, model_id: str) -> ModelState      # loading|ready|stopping|stopped|crashed(+reason)
    def target(self, model_id: str) -> Target          # base_url, upstream_model_name, auth_header
    def logs(self, model_id: str) -> list[str]
    def loaded(self) -> list[str]
```
The Proxy always rewrites the request's `model` field to `target.upstream_model_name`. PID files, watchdogs and crash classification stay inside `LocalProcessBackend`.

**Wiring.** `app.py` builds a single `AppContext` dataclass in the lifespan, and routes get it through `Depends`. Core modules receive their collaborators as constructor arguments and never import `app` or `api/*`.

**Events.** `events.py` is an in-process pub/sub bus with these topics:
- `request.completed`, published by the Proxy with a `RequestRecord`. Subscribers are RequestLog, Metrics and the admin SSE feeds.
- `model.state` and `model.log`, published by the Backend.
- `download.progress`, published by Downloads.
- `binary.progress`, published by BinaryManager.
- `settings.changed`, published by Settings.

## 4. Components

| Component | Responsibility |
|---|---|
| **Settings** | Reads and writes `~/.strix/run/config.json` (`STRIX_HOME` overrides `~/.strix`). Saves are atomic and the file is mode 0600.<br>**Fields:**<br>• `host` (default `127.0.0.1`), `port` (default `8790`)<br>• `api_key_hash` (salted sha256, optional), `trust_loopback` (default `true`)<br>• `models_dirs` (default `[~/.strix/run/models]`), `extra_scan_dirs`<br>• `binary_tag`, `binary_variant`, `binary_path` (override)<br>• `log_body_ring_size` (default 50; 0 disables), `log_retention_days` (default 30)<br>• `restore_loaded` (default `true`), `upstream_idle_timeout_s` (default 120)<br>**File-only fields:** `host`, `binary_path`, `models_dirs` and `extra_scan_dirs` can be changed only by editing the file or through the CLI, never through the HTTP API.<br>**HF token:** not stored here. It comes from `HF_TOKEN` or from huggingface_hub's own token store (`hf auth login`). The dashboard can write to that store, and the API only ever reports it as set or unset.<br>**Live changes:** `settings.changed` is published so that auth, Registry and RequestLog pick up new values without a restart. |
| **Registry** | Inventory of local GGUF models, persisted in `models.json`.<br>**Each model has:** an immutable internal `id`, a filename-safe slug `[a-z0-9._-]+` derived from `owner--repo--quant` (e.g. `bartowski--qwen2.5-7b-instruct--q4_k_m`); `aliases`; path(s); source repo and quant; size; location (`store` or `scan`); and parsed GGUF metadata (architecture, `n_layer`, `n_head_kv`, head dim, trained context, sliding-window info, chat template present, embedding-only).<br>**Aliases:** the default alias is the short form `qwen2.5-7b-instruct:q4_k_m`. If it collides with an existing alias, `@owner` is appended (`qwen2.5-7b-instruct:q4_k_m@unsloth`). Users can add or rename aliases, e.g. `gpt-4o-mini`. Aliases are unique and are used only to resolve requests. Files, admin routes and log rows always use `id`.<br>**Split files:** a split GGUF is one model, loaded via shard 1.<br>**Rescans:** triggered by `settings.changed`. |
| **Presets** | One file per model at `presets/<id>.json`, holding:<br>• `ctx_per_request` (default min(trained ctx, 8192)), `n_gpu_layers` (default 999), `parallel` (default 2), `queue_max` (default 16)<br>• `flash_attn` (default auto), `cache_type_k` and `cache_type_v` (default f16)<br>• `mode` (`chat` or `embedding`)<br>• `chat_template` (a filename inside `~/.strix/run/templates/`)<br>• `extra_args` (allowlisted flags only, see Supervisor)<br>• default sampling parameters<br>Saved automatically with the last-used values. llama-server receives `-c ctx_per_request × parallel`, so every slot gets the full per-request context. |
| **Downloader** | **Search:** Hugging Face search for GGUF repos, with repo metadata cached in memory for the session.<br>**File list:** quant files are grouped (shards merged). Fit badges are fetched lazily when a repo is expanded, using remote header reads (incremental HTTP range requests and a custom KV parser) fed through the estimator.<br>**Download:** via `hf_hub_download` into `models_dirs[0]/<owner>--<repo>/`. It resumes interrupted downloads, checks free disk space first, and publishes progress on `download.progress`. Each file downloads in a watchdog-wrapped worker process (`strixrun.hf_worker`) that reports progress through `hf_hub_download(tqdm_class=...)` as JSON lines. Cancelling stops the worker: the step-21 spike showed the Xet backend ignores exceptions raised from progress callbacks, so in-process cancellation is impossible.<br>**Validation:** the requested filenames must exist in the repo listing, and the resolved real path must sit under `models_dirs[0]`.<br>**Errors:** gated repo → link to accept the license; 429 → advice to set `HF_TOKEN`. |
| **BinaryManager** | **Manifest:** an in-repo `manifest.json` maps `tag → {variant → asset, archive format (zip/tar.gz), sha256}`. The sha256 values are computed by `scripts/pin_binary.py`, since upstream doesn't publish checksums. The v1 variants are `macos-arm64` (Metal), `win-cuda-12-x64` (plus the cudart asset), `win-cpu-x64` and `linux-cpu-x64`. Variant detection on Windows looks for NVIDIA via `nvidia-smi`, falling back to CPU.<br>**Install** (only tags present in the manifest are accepted):<br>1. Download to a temporary folder.<br>2. Verify the sha256.<br>3. Extract safely: tar with `filter="data"`; zip with a member-path check and execute bits restored from `external_attr`; symlinks preserved on POSIX.<br>4. On macOS, strip `com.apple.quarantine` recursively.<br>5. Smoke test with `llama-server --version`.<br>6. Move into `bin/<tag>/` and set `binary_tag` in the config. No `current` symlink is used.<br>A failure at any step leaves the current binary untouched. Rollback sets the previous tag. A `binary_path` in the config file overrides all of this.<br>**When it runs:** the install happens when `serve` starts if no binary is present, with console and SSE progress. If it fails, `serve` still starts and the Settings page shows the error and a retry button. |
| **MemoryEstimator** | **Formula:** `weights` = sum of GGUF file sizes; `kv` = 2 × n_layer × (ctx_per_request × parallel) × n_head_kv × head_dim × bytes(cache_type), with a sliding-window correction when the metadata reports it; `compute` ≈ 0.5 GB + 2% of weights.<br>**Available memory:**<br>• Apple silicon with `n_gpu_layers>0`: min(psutil `available`, the Metal working-set limit). The limit is `iogpu.wired_limit_mb` if set, else 0.67 × total RAM.<br>• NVIDIA: `nvidia-smi` free VRAM.<br>• Otherwise: psutil `available`.<br>**Verdict:** `ok`, `tight` (above 85% of available) or `over`. `tight` and `over` warn, and the load continues only after the user confirms. |
| **Supervisor** (`LocalProcessBackend`) | **Spawn:** builds an argument list (never a shell) from fixed flags plus allowlisted `extra_args`: `llama-server -m <path> --host 127.0.0.1 --port <p> --api-key <random> --jinja -c <ctx×parallel> -ngl <n> --parallel <n> [--embeddings] [--chat-template-file <templates/x>]`.<br>**`extra_args` allowlist:** sampling and performance flags only (e.g. `--threads`, `--batch-size`, `--ubatch-size`, `--rope-*`, `--no-mmap`, `--mlock`, `--defrag-thold`, `--kv-unified`). Anything that reads or writes files, such as `--log-file`, `--slot-save-path`, `--lora` or `-m`, is rejected.<br>**Orphan protection:** each child is started through `watchdog.py`, a tiny Python wrapper that spawns llama-server and kills it when its stdin pipe from StrixRun closes. This covers parent death on every OS. Windows adds a Job Object with KILL_ON_JOB_CLOSE, and Linux adds pdeathsig.<br>**Ports:** a free-port pick, retried up to 3 times on bind failure.<br>**Readiness:** polls `/health` every 250 ms, with a load timeout of 300 s by default.<br>**Logs and state:** stderr goes into a 500-line ring per model and is published on `model.log`. States move loading → ready → stopping → stopped, or to `crashed` with a classified reason (OOM, unknown architecture, missing cudart, port in use, generic), published on `model.state`.<br>**PID files:** each records the pid, process create_time and exe name. At startup, a process is reaped only if all three still match.<br>**Stop:** CTRL_BREAK or SIGTERM, then kill after 10 s.<br>**Restart:** the set of loaded models is saved to `state.json`. If `restore_loaded` is on, those models are reloaded at startup with their presets. Restores skip the confirm prompt but are logged. |
| **Proxy** | **Resolution:** alias → `id` → Backend state. Unknown → 404; not loaded → 409; loading → 503; crashed → 409 `model_crashed`.<br>**Concurrency:** a per-model `asyncio.Semaphore(parallel)` plus a bounded wait queue. A full queue returns 429.<br>**Forwarding:** uses `httpx.AsyncClient.stream` with a connect timeout and an idle timeout between chunks (`upstream_idle_timeout_s`). It rewrites `model` and adds the child's auth header, and injects `stream_options.include_usage=true` on streaming requests.<br>**Streaming:** bytes pass through a `StreamingResponse` with `Cache-Control: no-cache` and `X-Accel-Buffering: no`, and every chunk is fed to StreamMeter.<br>**Failures:** a failure before the first byte returns 502 `upstream_error`. After the first byte, the proxy emits an OpenAI-shaped SSE `error` chunk and closes. If the client disconnects, the upstream stream is closed and the record's status is `cancelled`.<br>**Output:** a `RequestRecord` published on `request.completed`. |
| **StreamMeter** | A pure, side-effect-free parser of SSE and JSON chunks. It returns TTFT (time to the first content or tool-call delta), usage (prompt and completion tokens), llama-server `timings` and the finish reason. |
| **RequestLog** | **Storage:** a SQLite `requests` table, with a migrations table: `id`, `ts`, `model_id`, `alias_used`, `endpoint`, `stream`, `status` (ok, error or cancelled), `error`, `client_ip`, `key_id`, `prompt_tokens`, `completion_tokens`, `ttft_ms`, `total_ms`, `tok_per_s`, `queue_ms`.<br>**Body ring:** an in-memory ring of the last N request and response bodies (never headers). Bodies are never written to disk.<br>**Retention:** a sweep deletes rows older than `log_retention_days`.<br>Subscribes to `request.completed`. |
| **Security middleware** | Pure ASGI (not `BaseHTTPMiddleware`). Auth checks the raw socket peer. uvicorn runs with `proxy_headers=False`.<br>**Host guard:** rejects any `Host` not in {localhost, 127.0.0.1, [::1], the configured host or LAN IP}:port. This blocks DNS rebinding.<br>**Origin guard:** on `/admin` and `/upstream`, requests that aren't GET must carry a same-origin `Origin` (or none); POSTs must also be `Content-Type: application/json` (PUT/PATCH/DELETE always need a CORS preflight, which is never granted). No CORS headers are sent anywhere.<br>**Auth rule:**<br>• Requests with no key configured: loopback only, since startup and the settings API both refuse a LAN host without a key.<br>• Requests with a key configured: loopback peers are exempt only if `trust_loopback` is true, else they must authenticate. Everything else must present the key, as `Authorization: Bearer`, `x-api-key`, or the session cookie.<br>**Session cookie:** `POST /admin/session` (key in the body) sets an HttpOnly, SameSite=Strict `strixrun_session` cookie, the HMAC of a server secret. It works for EventSource and for fetch.<br>**Public paths:** the dashboard shell (`/`, `/assets/*`, `/favicon*`) and `POST /admin/session`.<br>**Key handling:** keys are generated server-side as 32 random bytes, shown once, stored as a salted sha256, and compared with `hmac.compare_digest`. |
| **Metrics** | `/metrics` sits behind auth and subscribes to the event bus. It reports requests by model and status, histograms of TTFT and tokens per second, queue depth, loaded models, and the estimated memory of each model. |
| **Upstream passthrough** | `/upstream/{model}/{path}` forwards only `GET /health`, `GET /props`, `GET /slots`, `POST /tokenize` and `POST /detokenize`. Each call is published as a `RequestRecord` with `endpoint=upstream`. Anything else returns 404. |
| **CLI** (Typer) | **Server and key:**<br>• `serve [--port]`: the host comes from config; `--host` writes it to config after a key check.<br>• `key set\|clear\|rotate`: writes config directly, so it works while the server is down.<br>**Through the admin API:**<br>• `load <alias\|id> [--ctx … --parallel … --yes --no-wait]`: blocks with a progress line until ready. On a crash it exits non-zero, printing the reason class and the stderr tail.<br>• `unload <model>`, `ps`.<br>**Service:** `service install\|uninstall` creates a launchd agent on macOS, a Task Scheduler logon task on Windows, or a systemd user unit on Linux.<br>**Settings:** `config set <field> <value>` sets file-only settings such as `binary_path` and `models_dirs`. |
| **Dashboard** | **Stack:** React 19, Vite and TypeScript, with Radix and lucide icons. Plain CSS using StrixCode's `tokens.css`, owl branding and design brief (dark theme, single lime accent `#d4f246`, no gradients or emoji).<br>**Security:** a strict CSP. Untrusted text (bodies, Hugging Face README and names, stderr, templates) is never rendered as raw HTML, and markdown is sanitized.<br>**Auth:** on a 401, a key prompt posts to `/admin/session`.<br>**Pages:**<br>• **Models:** local list with states; a load drawer with the preset form, a live estimate from the estimate endpoint, a confirm on `tight`, and load progress plus crash stderr from the events feed; unload; alias editing; delete. The empty state reads "No models yet: search Hugging Face" with the Hugging Face search panel open, and the panel shows fit badges and download progress.<br>**Requests:** a live table with a filter, a detail drawer, and "Replay in playground".<br>**Playground:** streaming chat, sampling controls, and a picker of loaded models. With nothing loaded, it links to Models.<br>**Settings:** port, API key, Hugging Face token (set or unset only), retention and ring size, restore-loaded, binary version with upgrade and rollback; a read-only view of the file-only fields with the CLI command to change each.<br>**Build:** shipped as package data. A hatch build hook runs `npm run build` when the package is built from source, and the server mounts the static files only if the build directory exists. |

## 5. API surface

**OpenAI-compatible** (proxied):
- `POST /v1/chat/completions`, supporting `stream`, `tools`/`tool_calls` and `response_format` (`json_object` and `json_schema`).
- `POST /v1/completions`.
- `POST /v1/embeddings`. Only for models in embedding mode; a chat-mode model returns 400.
- `GET /v1/models`, served by StrixRun itself. It returns `{"object":"list","data":[...]}` with one entry per alias of each loaded model. With `?all=1` it adds every downloaded model, each with a `status` field.

**Admin** (JSON; SSE where noted):
- **Session:** `POST /admin/session` and `DELETE /admin/session`.
- **Models:**
  - `GET /admin/models`
  - `PATCH /admin/models/{id}` (aliases)
  - `DELETE /admin/models/{id}`. Refused with 409 while the model is loaded and with 403 for models from scan folders. Otherwise it removes all shards, the registry entry, the aliases and the preset.
  - `POST /admin/models/{id}/estimate` (body: preset fields)
  - `POST /admin/models/{id}/load` (body: preset plus `confirm`)
  - `POST /admin/models/{id}/unload`
  - `GET /admin/models/{id}/logs`
  - `GET /admin/models/events` (SSE: `model.state` and `model.log`)
  - `GET /admin/ps`
- **Hugging Face:** `GET /admin/hf/search?q=`, `GET /admin/hf/repo/{owner}/{repo}/files` (with fit badges), `POST /admin/downloads`, `DELETE /admin/downloads/{job}` (cancel), `GET /admin/downloads/events` (SSE).
- **Requests:** `GET /admin/requests`, `GET /admin/requests/{id}`, `GET /admin/requests/events` (SSE).
- **Settings:** `GET /admin/settings` and `PUT /admin/settings`. File-only fields are returned read-only, and a PUT that includes them gets 400. Also `POST /admin/settings/api-key` (generate, rotate or clear) and `PUT /admin/settings/hf-token`.
- **Binary:** `GET /admin/binary`, `POST /admin/binary/install` (tag must be in the manifest), `POST /admin/binary/rollback`, `GET /admin/binary/events` (SSE).

**Passthrough:** `/upstream/{model}/{path}`, limited to the allowlisted routes in §4.

## 6. Data flow

**Inference request:**
1. Host and Origin guard, then auth.
2. Resolve the alias:
   - Unknown model: 404 `model_not_found`.
   - Downloaded but not loaded: 409 `model_not_loaded`, telling the user to load it in the dashboard or run `strixrun load <alias>`.
   - Loading: 503 with `Retry-After`.
   - Crashed: 409 `model_crashed`, with the reason and the reload command.
3. Take a slot, or queue for one. If the queue is full, return 429.
4. Proxy the request and stream it back through StreamMeter.
5. Publish a `RequestRecord`. RequestLog, Metrics and the SSE feeds all receive it.

**Load:**
1. Estimate memory. If the verdict isn't `ok` and the request isn't confirmed, return 409 `needs_confirmation` with the estimate.
2. Make sure the binary exists. If it doesn't, return 409 `binary_missing` pointing to Settings.
3. Spawn the child through the watchdog.
4. Publish `model.state` and `model.log` events.
5. The model ends up `ready`, or `crashed` with the classified stderr tail.
6. Save the set of loaded models to `state.json`.

**Startup:**
1. Validate the config. Refuse to start with a LAN host and no key, and name `strixrun key set` in the error.
2. Reap orphaned children from the PID files, checking pid, create_time and exe.
3. Install the binary if it is missing (non-fatal).
4. Rescan the Registry.
5. If `restore_loaded` is on, reload the models listed in `state.json`.

**Download:**
1. Validate the repo and files against the listing.
2. Check free disk space.
3. Run `hf_hub_download` for every shard, publishing progress.
4. Parse the GGUF metadata.
5. Register the model with its `id`, a default alias and a default preset.

## 7. Error handling

- All `/v1` errors use the OpenAI error shape `{"error": {"message", "type", "code"}}`.
- **Child crash:** the model is marked `crashed` and is not restarted automatically. An in-flight request gets a 502 if nothing has been sent yet, or an SSE error chunk if streaming has started. Later requests get 409 `model_crashed`. The dashboard shows the reason and the stderr tail.
- **Hung child:** the idle timeout between chunks ends the request and frees its slot. The model's state doesn't change, but the event is logged with status `error`.
- **Client disconnect:** the upstream stream is closed and the request is logged as `cancelled`.
- **Downloads:** resumable. Gated repos link to the license page. A 429 suggests setting `HF_TOKEN`. Disk space is checked before starting. Paths are validated, and downloads can be cancelled.
- **Binary install:** the current binary is untouched on any failure, and archives are extracted safely.
- **Settings:** the API rejects a LAN host without a key and rejects writes to file-only fields. The CLI `key set` works offline.
- **Shutdown** (SIGINT, SIGTERM or CTRL_BREAK): stop accepting requests, save `state.json`, unload models gracefully (10 s each), remove the PID files. The watchdogs cover the case where StrixRun dies abruptly.

## 8. Testing strategy

- **Unit tests (pytest):**
  - Settings round-trip and 0600 mode; file-only enforcement.
  - Slug and alias collisions, including on Windows-hostile characters.
  - Preset `-c` maths.
  - The estimator against hand-computed fixtures, including the Metal cap.
  - StreamMeter on recorded chunk fixtures.
  - Manifest lookup, variant detection and safe extraction (zip-slip and exec-bit cases).
  - GGUF parsing, using a fixture generated with `GGUFWriter`, plus the remote parser.
  - Security middleware: rebinding `Host`, cross-site `Origin`, the `trust_loopback` matrix, the cookie session, and incremental streaming flush.
  - `extra_args` allowlist and template path containment.
  - Error mapping.
- **Integration tests (pytest plus a fake llama-server):** `tests/fake_llama_server.py` accepts and ignores real llama-server flags. It can be launched as `[sys.executable, fake.py, ...]`, and knobs via env or flags control loading delay, token pace, hang, crash, and which stderr signature it emits.
  - Covers: watchdog orphan kill (all 3 OSes in CI), supervisor lifecycle, the proxy (TTFT, cancel, idle timeout, mid-stream error chunk), the queue and 429, every 404/409/503/502 path, RequestLog, restore-on-restart, and the admin routes including delete invariants.
- **End-to-end tests (opt-in, `-m e2e`):**
  - Install the pinned binary.
  - Pull Qwen2.5-1.5B-Instruct Q4_K_M and nomic-embed-text-v1.5 Q4, then load them.
  - Use the `openai` SDK for chat, streaming, a tool call (`tool_choice` required, temperature 0, fixed seed), `json_schema` output with validation, and embeddings with the expected dimension.
- **CI:** GitHub Actions on macOS-arm64, Windows-x64 and Ubuntu-x64 runs the unit and integration tests. The end-to-end tests run on Ubuntu (CPU), with models cached and an `HF_TOKEN` secret. A weekly job checks that every manifest asset URL still resolves.
- **Manual testing:** the author tests Metal (M4) and CUDA (Windows) against the success criteria.
- **Dashboard:** Vitest for components and the API client, including the SSE and 401 → session flow. One Playwright test covers the full loop against a seeded fake backend.

## 9. Assumptions

- **Stack:** Python 3.12 or later, managed with `uv`. Dependencies are FastAPI, uvicorn, httpx, huggingface_hub, psutil, Typer and `gguf`; SQLite comes from the standard library.
- **Distribution:** `uv tool install strixrun` from PyPI or a git URL. The hatch build hook bundles the dashboard either way.
- **Port:** 8790 by default.
- **Default preset:** `ctx_per_request = min(trained, 8192)`, `parallel = 2` (so `-c 16384`), all layers on the GPU, flash attention on auto, f16 KV cache.
- **Model modes:** a model is either a chat model or an embedding model, according to its preset.
- **Storage:** downloads go to `~/.strix/run/models/<owner>--<repo>/`. Scan folders are only ever read, never written.
- **API key:** one global key.
- **Design assets:** StrixCode's tokens, fonts and owl images are copied, not shared. The sister apps already work this way.

## 10. Non-goals (v1)

- MLX or any engine other than llama.cpp.
- Loading a model automatically on request, or unloading it after an idle timeout. (Restoring previously loaded models on restart is in scope.)
- Multiple API keys, or per-key quotas and rate limits.
- Ollama-compatible or Anthropic-compatible APIs.
- StrixCode integration beyond the shared look and the `~/.strix/` namespace.
- Vision, image, audio or reranker models.
- GPU testing on Linux, ROCm, or Vulkan builds.
- A desktop or menu-bar wrapper.
- CLI commands for pulling or listing models (downloading is done in the dashboard).
- Exporting router-mode INI files.

## 11. Later ideas

- A per-model flag to load automatically, and unloading after an idle period.
- Multiple keys with allow-lists and rate limits, and a separate admin key.
- A llama.cpp router-mode `Backend`.
- Importing llama-swap and router-mode INI configs, and exporting INI.
- Ollama-compatible endpoints.
- A StrixCode local provider.
- An MLX backend.
- Calibrating the estimator against measured RSS.
- Vision models (mmproj).
- `strixrun pull` and `strixrun models` CLI commands.
- Linux Vulkan builds.
- Charts on the Requests page.
