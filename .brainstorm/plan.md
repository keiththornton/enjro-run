# StrixRun Plan

**Goal:** Ship v1 of StrixRun: a cross-platform local llama.cpp server with an OpenAI-compatible API and a light React dashboard, meeting the four success criteria in design §2.

**Approach:** One Python process (FastAPI/uvicorn) supervises one watchdog-wrapped `llama-server` child per loaded model, behind a `Backend` protocol. It proxies `/v1/*` with metrics captured for each request, which travel over an in-process event bus. The same port serves a static React dashboard. Build order:
1. Security and the API core, tested against a fake llama-server.
2. Real binaries and models.
3. The dashboard.
4. CLI, service and shipping.

**Design:** `.brainstorm/design.md` is the source of truth; this plan doesn't restate it. It was revised after the multi-agent review.

**Research picks:** request observability with replay; memory-fit badges from remote GGUF headers; predictable residency with aliases; an own process manager behind an interface; `/metrics` and an allowlisted `/upstream`; a pinned, verified binary manifest.

## File structure

```
pyproject.toml                          — deps, `strixrun` entry point, hatch build hook (dashboard), package-data
hatch_build.py                          — runs `npm ci && npm run build` in web/ when building from source; skips if web_dist present
scripts/pin_binary.py                   — downloads manifest assets for a tag, computes sha256, writes manifest.json
src/strixrun/__init__.py                — version
src/strixrun/paths.py                   — STRIX_HOME resolution, all data paths
src/strixrun/settings.py                — config.json load/atomic save (0600), file-only fields, key gen/hash/verify, settings.changed
src/strixrun/events.py                  — in-process pub/sub bus + topic/record types
src/strixrun/context.py                 — AppContext dataclass (constructed in app lifespan)
src/strixrun/errors.py                  — OpenAI-shaped error types + handlers
src/strixrun/gguf_meta.py               — local GGUF parsing (gguf pkg) + remote incremental KV parser
src/strixrun/registry.py                — model inventory, immutable ids, aliases/collisions, models.json, rescans, delete
src/strixrun/presets.py                 — preset model, defaults, ctx×parallel, extra_args allowlist, template containment
src/strixrun/memory.py                  — estimator + probes (psutil, Metal wired limit, nvidia-smi)
src/strixrun/binaries/manifest.json     — tag → variant → asset/format/sha256
src/strixrun/binaries/manager.py        — variant detection, download/verify/safe-extract/quarantine/smoke-test, rollback
src/strixrun/backend/base.py            — Backend protocol, ModelState, Target
src/strixrun/backend/watchdog.py        — child wrapper: spawn llama-server, kill it on stdin EOF
src/strixrun/backend/procutil.py        — spawn via watchdog, Job Object/pdeathsig, graceful stop, port pick, PID files w/ create_time
src/strixrun/backend/supervisor.py      — LocalProcessBackend: lifecycle, health, stderr ring, crash classify, state.json
src/strixrun/streammeter.py             — pure SSE/JSON chunk parser → TTFT, usage, timings, finish reason
src/strixrun/proxy.py                   — resolution, slot semaphore + queue, forwarding w/ timeouts, error chunk, RequestRecord
src/strixrun/requestlog.py              — SQLite schema/migrations/queries, body ring, retention; subscribes to bus
src/strixrun/downloads.py               — HF search/cache, file listing + lazy fit badges, download jobs + progress, cancel
src/strixrun/security.py                — pure ASGI middleware: Host/Origin guards, auth rules, session cookie
src/strixrun/metrics.py                 — Prometheus exposition; subscribes to bus
src/strixrun/api/openai.py              — /v1 routes
src/strixrun/api/upstream.py            — allowlisted passthrough
src/strixrun/api/admin/__init__.py      — admin router assembly
src/strixrun/api/admin/session.py       — /admin/session
src/strixrun/api/admin/models.py        — models, estimate, load/unload, logs, events, ps, alias PATCH, DELETE
src/strixrun/api/admin/hf.py            — HF search, files, downloads + events
src/strixrun/api/admin/requests.py      — request list/detail/events
src/strixrun/api/admin/settings.py      — settings, api-key, hf-token
src/strixrun/api/admin/binary.py        — binary status/install/rollback/events
src/strixrun/app.py                     — app factory, lifespan (startup sequence §6, shutdown), conditional static mount, CSP header
src/strixrun/cli.py                     — Typer CLI: serve, key, config, load, unload, ps, service
src/strixrun/service.py                 — launchd / Task Scheduler / systemd generation + install/uninstall
tests/fake_llama_server.py              — fake llama-server (accepts real flags; env/flag knobs)
tests/fixtures/make_tiny_gguf.py        — generates tiny.gguf via GGUFWriter
tests/fixtures/chunks/                  — recorded llama-server SSE chunk fixtures
tests/unit/… tests/integration/… tests/e2e/test_openai_sdk.py
web/                                    — React 19 + Vite + TS dashboard
web/src/styles/tokens.css               — copied from strix-ide/src/styles/tokens.css
web/src/app/                            — shell, nav, routing, replay handoff store
web/src/api/                            — typed client, SSE helper, session/401 flow
web/src/pages/{Models,Requests,Playground,Settings}.tsx
web/src/components/                     — primitives on Radix + HfSearch, LoadDrawer, SafeText/Markdown (sanitized)
web/e2e/loop.spec.ts                    — Playwright full loop
.github/workflows/ci.yml                — 3-OS matrix (py unit+integration, web build/test), Ubuntu e2e w/ model cache
.github/workflows/manifest-check.yml    — weekly manifest URL check
README.md                               — install, quickstart, client snippets, LAN/key/tunnel notes, privacy, troubleshooting
```

## Milestones

- **M1, Secure API core against the fake:** `strixrun serve` proxies OpenAI requests to watchdog-wrapped fake children, with Host/Origin/auth guards, errors, a queue, timeouts, the event bus, request logging and restore-on-restart. Integration tests pass in CI on all 3 OSes.
- **M2, Real models:** the pinned binary installs safely; Hugging Face search, badges and download work; a real model loads with an estimate; the OpenAI SDK e2e test (chat, stream, tools, json_schema, embeddings) passes on the M4 and in Ubuntu CI.
- **M3, Dashboard loop:** in the browser: search → badge → download → load → playground → live log → replay, including from a LAN device via the key prompt.
- **M4, Ship:** CLI load/ps/key/service work, the Windows CUDA manual pass is done, the README is complete, and §2 is verified with evidence.

## Steps

### M1: Secure API core
- [x] 1. Scaffold the uv project, package layout, ruff and pytest, plus the CI workflow running pytest on the 3-OS matrix. Files: `pyproject.toml`, `src/strixrun/__init__.py`, `.github/workflows/ci.yml`. Done when `uv run pytest` passes with a trivial test locally and in CI.
- [x] 2. TDD: paths and settings. Covers `STRIX_HOME`; defaults (including `binary_path`, `trust_loopback`, `restore_loaded`, `log_retention_days`, `upstream_idle_timeout_s`); atomic save with mode 0600 (POSIX); file-only fields; key generate/hash/verify with `compare_digest`; refusing a LAN host without a key. Files: `paths.py`, `settings.py`, `tests/unit/test_settings.py`. Done when tests pass, including a 0600 assertion on POSIX and a LAN-host-without-key rejection.
- [x] 3. [P] TDD: event bus and AppContext skeleton. Files: `events.py`, `context.py`, `tests/unit/test_events.py`. Done when tests show multiple subscribers receive a published record and an unsubscribed queue is released.
- [x] 4. [P] TDD: OpenAI-shaped errors for 404 model_not_found; 409 model_not_loaded, model_crashed, needs_confirmation and binary_missing; 429; 502 upstream_error; 503 with Retry-After. Files: `errors.py`, `tests/unit/test_errors.py`. Done when tests assert the exact JSON and headers for each.
- [x] 5. [P] Fake llama-server. It accepts and ignores real llama-server flags (`-m -c -ngl --jinja --parallel --api-key --embeddings --chat-template-file …`) and launches as `[sys.executable, fake.py, …]`. It provides `/health` with a loading delay, chat stream and non-stream with usage and timings, tool-call deltas, embeddings, and checks the child `--api-key`. Knobs (env or flags) control token pace, hang after N tokens, crash after N tokens, and the stderr signature (oom, unknown_arch, cudart, port). Also record the chunk fixtures. Files: `tests/fake_llama_server.py`, `tests/fixtures/chunks/`. Done when it is launched manually with real flags and a curl stream ends in `[DONE]`, and when a wrong api-key gets 401.
- [x] 6. TDD: Registry (store and scan locations, immutable slug ids, default alias with `@owner` collision suffix, alias uniqueness, models.json) and Presets (defaults, `-c ctx×parallel`, `extra_args` allowlist, template path containment). Files: `registry.py`, `presets.py`, `tests/unit/test_registry.py`, `tests/unit/test_presets.py`. Done when tests pass, including that a colon-containing alias yields a Windows-safe preset filename, two owners of the same repo get distinct aliases, `--log-file` in `extra_args` is rejected, and `../x` as a template is rejected.
- [x] 7. TDD: watchdog and procutil. _(verified: CI green on macOS, Windows, Ubuntu)_ The watchdog kills the child on stdin EOF. Also covers the Windows Job Object, Linux pdeathsig, graceful stop then kill, free-port pick with retry, and PID files with create_time and exe plus a verified reap. Files: `backend/watchdog.py`, `backend/procutil.py`, `tests/integration/test_procutil.py`. Done when CI on all 3 OSes shows that SIGKILL/TerminateProcess of a harness parent leaves no fake child alive within 5 s, and when the reap skips a PID whose create_time doesn't match.
- [x] 8. TDD: Backend protocol and LocalProcessBackend. States (loading → ready → stopping → stopped, and crashed), `/health` polling with timeout, a 500-line stderr ring, crash classification, `target()` with the child api-key, and `state.json` save and restore. Files: `backend/base.py`, `backend/supervisor.py`, `tests/integration/test_supervisor.py`. Done when tests cover ready, load timeout, early exit, each classified crash, and restore-after-restart against the fake.
- [x] 9. [P] TDD: StreamMeter. Files: `streammeter.py`, `tests/unit/test_streammeter.py`. Done when it extracts TTFT index, usage, timings and finish_reason correctly from the recorded fixtures, including tool-call-only streams.
- [x] 10. TDD: Proxy. Covers resolution (404, 409 not_loaded, 409 crashed, 503), the semaphore and queue (429), model rewrite and child auth, `include_usage` injection, no-buffer headers, connect and idle timeouts, 502 before the first byte, an SSE error chunk after it, cancel on disconnect (`cancelled`), and publishing a RequestRecord. Files: `proxy.py`, `tests/integration/test_proxy.py`. Done when tests pass, including a mid-stream disconnect where the fake observes cancellation, a hang knob that frees its slot after the idle timeout, and a crash knob that produces an error chunk.
- [x] 11. TDD: RequestLog. Covers the schema and migrations, writes from the bus, list and filter queries, the body ring (size 0 disables it, no headers stored) and the retention sweep. Files: `requestlog.py`, `tests/unit/test_requestlog.py`. Done when tests pass for ring eviction, ring size 0 and retention deletion.
- [x] 12. TDD: security middleware. Covers the Host guard, the Origin and content-type guard on non-GET `/admin` and `/upstream`, the auth matrix (no key / key with trust_loopback on and off / bearer / x-api-key / cookie), the public shell paths, the `/admin/session` cookie, `proxy_headers=False`, and no CORS headers. Files: `security.py`, `tests/unit/test_security.py`. Done when tests pass for a rebinding Host (`evil.com:8790` → 403), a cross-site Origin POST (→ 403), a text/plain POST (→ 415), a LAN client reaching `/` without a key (200) and `/admin/ps` without a key (401), and incremental streaming flushing through the middleware.
- [x] 13. App factory and lifespan: AppContext construction, the startup sequence (validate config → reap → rescan → restore), shutdown, a conditional static mount, the CSP header, and the `/v1` routes (chat, completions, embeddings with 400 for chat-mode models, and `/v1/models` in OpenAI list shape with `?all=1`). Files: `app.py`, `api/openai.py`, `tests/integration/test_v1.py`. Done when the official `openai` SDK against fake children passes chat, stream and embeddings; `/v1/models` returns `{"object":"list","data":[…]}`; and the app starts with no `web_dist` present.
- [x] 14. Admin routes, M1 set: session; models list, load (estimate gate stubbed to ok), unload, logs, `models/events` SSE, ps, PATCH aliases, and DELETE (409 if loaded, 403 for scan models, removes all shards, aliases and preset); requests list/detail/events SSE. Files: `api/admin/__init__.py`, `api/admin/{session,models,requests}.py`, `registry.py` (delete), `tests/integration/test_admin.py`. Done when tests show: load via admin produces state events and then a request on the requests SSE; delete invariants hold, with redownload and load of the same id working afterwards; and alias rename resolves on `/v1`.
- [x] 15. [P] `/upstream` allowlisted passthrough and `/metrics`, both subscribing to the bus. Files: `api/upstream.py`, `metrics.py`, `app.py` (mount), `tests/integration/test_upstream_metrics.py`. Done when `/upstream/<m>/props` reaches the fake and is logged with `endpoint=upstream`, `/upstream/<m>/slots?action=save` returns 404, and `/metrics` exposes request counts, the TTFT histogram, queue depth and loaded models after two requests.
- [x] 16. Minimal CLI `serve`, plus the M1 checkpoint. _(verified: CI green on macOS, Windows, Ubuntu)_ Files: `cli.py`. Done when `uv run strixrun serve` starts and `curl /v1/models` returns `{"object":"list","data":[]}`; when a LAN host without a key exits with the `strixrun key set` hint; and when CI is green on all 3 OSes.

### M2: Real models
- [x] 17. TDD: GGUF metadata. Generate the fixture with `GGUFWriter`. Local parsing via `gguf`; a custom incremental remote KV parser using HTTP ranges; shard detection; embedding-only detection; sliding-window keys. Files: `gguf_meta.py`, `tests/fixtures/make_tiny_gguf.py`, `tests/unit/test_gguf_meta.py`. Done when tests pass on the fixture and a network-marked test reads a real HF GGUF's metadata in under 2 MB transferred.
- [x] 18. TDD: MemoryEstimator. Covers the formula with ctx×parallel, cache-type bytes, the sliding-window correction, the Metal wired-limit cap (sysctl `iogpu.wired_limit_mb`, else 0.67×RAM), `nvidia-smi` free VRAM, and the verdicts. Files: `memory.py`, `tests/unit/test_memory.py`. Done when tests match hand-computed fixtures for Llama-3-8B and Qwen2.5-1.5B shapes within 1%, and a simulated 16 GB Mac caps available memory at ≤ 0.67×total.
- [x] 19. BinaryManager part A: `scripts/pin_binary.py`, a provisional manifest (newest release tag; variants macos-arm64, win-cuda-12-x64 plus cudart, win-cpu-x64, linux-cpu-x64; archive format and sha256), variant detection, and the weekly manifest-check workflow. Files: `scripts/pin_binary.py`, `binaries/manifest.json`, `binaries/manager.py` (detect + lookup), `.github/workflows/manifest-check.yml`, `tests/unit/test_binaries.py`. Done when the script produces the manifest, unit tests for lookup and detection pass, and the manifest-check workflow passes on a manual dispatch.
- [x] 20. BinaryManager part B: download → verify → safe extract (tar `filter="data"`, zip member check, exec bits from `external_attr`, POSIX symlinks) → strip quarantine → smoke test → move to `bin/<tag>/` and set `binary_tag`; rollback; tags restricted to the manifest; binary progress events. Wire install-on-serve (non-fatal) plus the admin binary routes. Files: `binaries/manager.py`, `api/admin/binary.py`, `app.py`, `tests/unit/test_binaries.py`. Done when unit tests cover zip-slip rejection, exec-bit restoration and an unknown tag (400); a fresh `strixrun serve` on the M4 installs the binary with console progress; and `llama-server --version` runs from `bin/<tag>/`.
- [x] 21. Downloads spike: check the progress options of `hf_hub_download` against the installed huggingface_hub (callback vs polling the `.incomplete` file size), including on a Xet-backed repo. Files: `downloads.py` (progress helper), `tests/unit/test_downloads.py`. Done when a network-marked test shows at least 5 increasing progress events while downloading a ~100 MB file.
- [x] 22. Downloads: HF search with a session cache, quant grouping with shards merged, lazy fit badges per repo, jobs with a disk precheck, path containment, cancel, gated and 429 mapping, and registration on completion. Wire `api/admin/hf.py` and the real estimate and confirm in `api/admin/models.py` (`/estimate`, load 409 needs_confirmation, 409 binary_missing). Files: `downloads.py`, `registry.py`, `api/admin/hf.py`, `api/admin/models.py`, `tests/unit/test_downloads.py`, `tests/integration/test_admin.py`. Done when tests (mocked hub) pass, including a `../` filename rejected and a tight load returning 409 that then succeeds with `confirm`; and when a manual admin-API download of Qwen2.5-1.5B-Instruct Q4_K_M registers it with metadata.
- [x] 23. Admin settings routes: GET with file-only fields marked read-only; PUT rejecting file-only fields and a LAN host without a key; api-key generate/rotate/clear; hf-token set and unset via huggingface_hub's store (masked on read). Files: `api/admin/settings.py`, `tests/integration/test_admin_settings.py`. Done when tests show a PUT of `binary_path` returns 400, the token is never returned in plaintext, and key rotation invalidates the old session cookie.
- [x] 24. E2E with real models: install the binary; pull Qwen2.5-1.5B-Instruct Q4_K_M and nomic-embed-text-v1.5 Q4; load both; use the `openai` SDK for chat, stream, a tool call (`tool_choice` required, temperature 0, seed), validated json_schema output, and embeddings with the expected dimension. Add an Ubuntu CI job with a model cache and an `HF_TOKEN` secret. Then re-pin the manifest to the tag that passed. Files: `tests/e2e/test_openai_sdk.py`, `.github/workflows/ci.yml`, `binaries/manifest.json`. Done when `uv run pytest -m e2e` passes on the M4 and in Ubuntu CI on 3 consecutive runs.

### M3: Dashboard
- [x] 25. Scaffold `web/` with React 19, Vite and TS, Radix and lucide. Copy the tokens, fonts and owl assets from strix-ide. Build the app shell, nav, routing and the replay handoff store, plus primitives and a `SafeText`/sanitized Markdown component. Vite proxies to :8790. Add the hatch build hook. Files: `web/**`, `hatch_build.py`, `pyproject.toml`. Done when `uv build` produces a wheel containing `web_dist/`, and `strixrun serve` shows the shell with the Strix palette and a CSP header.
- [ ] 26. Typed API client: admin and `/v1` fetchers; an SSE helper (EventSource with the session cookie); a 401 → key prompt → `POST /admin/session` flow. Files: `web/src/api/**`. Done when Vitest covers SSE parsing and the 401 → session → retry flow.
- [ ] 27. Models page, local list: states from `models/events`; a load drawer with the preset form showing per-request context, a live estimate via `/estimate`, a confirm dialog on tight, progress, and crash stderr; unload; alias editing; delete with confirmation; the empty state. Files: `web/src/pages/Models.tsx`, `web/src/components/LoadDrawer.tsx`. Done when, on the M4, a real model loads from the browser, the estimate updates as context changes, and a forced crash (an invalid `extra_args` value that passes the allowlist) shows the reason and stderr.
- [ ] 28. HF search panel: search, quant rows with fit badges, gated license links, downloads with progress and cancel. Files: `web/src/components/HfSearch.tsx`, `web/src/pages/Models.tsx`. Done when Qwen2.5-1.5B can be found, badged and downloaded entirely from the browser.
- [ ] 29. Requests page: a live table, filters, a detail drawer (metrics, plus the body if still in the ring, rendered via SafeText), and "Replay in playground", which writes the replay store. Files: `web/src/pages/Requests.tsx`. Done when a curl request appears within 1 s with TTFT and tok/s, and Replay opens the Playground with its messages.
- [ ] 30. Playground: a picker of loaded models (empty state links to Models), streaming chat, sampling controls from the preset, and consuming the replay store. Files: `web/src/pages/Playground.tsx`. Done when streaming renders token by token and a replayed request reproduces its messages.
- [ ] 31. Settings page: port; API key generate/rotate/clear (shown once); the HF token (set/unset); retention and ring size; restore-loaded; binary version with upgrade, rollback and progress; read-only file-only fields with their CLI hints. Files: `web/src/pages/Settings.tsx`. Done when generating a key in the browser and setting `host` via the CLI lets another LAN device open the dashboard, pass the key prompt, and see live request events.
- [ ] 32. Playwright full loop against a seeded fake backend (fixture server with a pre-registered fake model): load → playground message → request visible in the log → replay. Files: `web/e2e/loop.spec.ts`, `tests/fixtures/seeded_server.py`, `.github/workflows/ci.yml`. Done when it passes in Ubuntu CI.

### M4: Ship
- [ ] 33. CLI: `key set|clear|rotate` (offline config write); `config set` for file-only fields; and via the admin API, `load` (blocks with progress; non-zero exit with the reason class on crash; `--yes`, `--no-wait`), `unload` and `ps`. Files: `cli.py`, `tests/integration/test_cli.py`. Done when tests pass, `strixrun key set` works with the server stopped, and `strixrun ps` shows a loaded model from a second terminal.
- [ ] 34. `strixrun service install|uninstall` for launchd, Task Scheduler and systemd. Files: `service.py`, `cli.py`, `tests/unit/test_service.py`. Done when the generated-file tests pass, and on the M4 the server is up after logout and login with previously loaded models restored, and uninstall removes the service.
- [ ] 35. Windows CUDA manual pass: `uv tool install` → CUDA variant plus cudart auto-installed on serve → a 7B model loaded fully offloaded → the e2e SDK suite run against it → StrixRun killed in Task Manager with no `llama-server.exe` left → a preset with an aliased model saved and reloaded (colon-safe filenames). Timebox fixes to 1 day and turn anything larger into issues. Files: fixes as found. Done when the checklist is recorded in the README under "Tested platforms".
- [ ] 36. README: install; quickstart; OpenAI SDK, Open WebUI and Continue snippets; LAN plus key setup; the reverse proxy/tunnel note (`trust_loopback=false`); aliases; a privacy note (bodies are memory-only, rows keep client_ip for N days, ring size 0); troubleshooting (gated repos, HF_TOKEN, OOM and the Metal limit, per-request context). Check the PyPI name. Files: `README.md`. Done when a fresh macOS user account goes from install to a first chat completion by following it verbatim.
- [ ] 37. Verification pass against §2 with fresh evidence: Open WebUI or Continue with tool use; the full dashboard loop on the M4; LAN with and without the key, plus the dashboard from a second device; M4 Metal and Windows CUDA manual results and Linux CI green. Done when all four items are checked off with notes and screenshots in `.brainstorm/verification.md`.

## Risks & mitigations

| Risk | Likelihood / blast | Mitigation |
|---|---|---|
| Browser attacks on loopback (CSRF, DNS rebinding) leading to settings changes | Medium / host compromise | Host and Origin guards, a JSON-only rule for writes, no CORS, file-only fields for executable paths, an `extra_args` allowlist (steps 2, 6, 12, 23). |
| llama.cpp asset names or formats change | High over time / install broken | Pinned manifest with format and sha256, the pin script, a weekly URL check, a smoke test before switching, rollback, and the `binary_path` override (steps 19, 20, 24). |
| Orphaned children holding GPU or unified memory | Medium / OOM on next load | Watchdog on stdin EOF (all OSes), Job Object and pdeathsig, PID reap verified by create_time, CI kill test (step 7), Windows manual test (step 35). |
| SSE buffering, missed disconnects, hung children holding slots | Medium / core UX | Pure ASGI middleware, flush test, cancel test, idle timeout, error chunk (steps 10, 12). |
| Memory estimate wrong (SWA, hybrid architectures, Metal limit) | High / misleading verdicts | Metal cap, SWA correction, labelled as an estimate, warns but never blocks, fixtures (step 18). Calibration is a later idea. |
| Tool calling fails on some templates, or is flaky in CI | Medium / criterion 1 at risk | `--jinja` always on, per-preset templates directory, a 1.5B model with `tool_choice` required, temperature 0 and a seed, 3 consecutive green runs (step 24). |
| HF rate limits, gated repos, missing progress hook, Xet | Medium / search or download feels broken | Session cache, lazy badges, HF_TOKEN via the HF store, a progress spike (step 21), clear error mapping (step 22). |
| Binary extraction loses exec bits or symlinks, or gets quarantined | High on first install / Mac broken | Format-aware safe extraction, exec bits from external_attr, recursive quarantine strip, smoke test (step 20). |
| LAN exposure without a key, or via a same-host tunnel | Low–medium / anyone can use the GPU | Startup and settings refusal, the `trust_loopback` setting, auth on the raw socket peer, README tunnel note (steps 2, 12, 36). |
| User locks themselves out (LAN host, no key) | Medium / server won't start | The settings API rejects it, offline `key set`, a startup error naming the fix (steps 2, 16, 33). |
| PID reuse leads to killing an unrelated process | Low / user data loss | PID files check create_time and exe (step 7). |
| Dashboard XSS leaks the session | Low–medium / admin takeover | CSP, SafeText and sanitized markdown, HttpOnly SameSite=Strict cookie (steps 12, 25). |
| Dashboard scope creep | Medium / schedule | Four pages only, no charts in v1. |

## Later ideas

- A per-model auto-load flag and idle unload.
- Multiple keys with allow-lists and rate limits, and a separate admin key.
- A router-mode backend.
- llama-swap config import, plus router-mode INI import and export.
- Ollama-compatible endpoints.
- A StrixCode local provider.
- An MLX backend.
- Estimator calibration against measured RSS.
- Vision models (mmproj).
- `strixrun pull` and `strixrun models` commands.
- Linux Vulkan builds.
- Charts on the Requests page.

## Open questions

- **Exact llama.cpp tag:** provisionally pinned in step 19, finalized in step 24 to whichever tag passes e2e.
- **PyPI name availability:** checked in step 36; the fallback is installing from the git URL.
- **License:** the author decides before the repo goes public.
- **Does the pinned tag support `--kv-unified`?** Checked in step 24. If it does, it can be added to the `extra_args` allowlist as an option; the default stays `-c ctx×parallel`.
