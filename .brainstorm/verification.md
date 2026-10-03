# StrixRun v1 — Verification against design §2

Date: 2026-10-03. Machine: Apple M4, 16 GB, macOS 27.0.1. llama.cpp b11146 (pinned).

## Fresh test runs (this pass)

| Suite | Result |
|---|---|
| `uv run pytest` (unit + integration, fake llama-server) | 284 passed |
| `uv run ruff check` | clean |
| `uv run pytest -m e2e` on the M4 (real llama.cpp, Metal; Qwen2.5-1.5B-Instruct Q4_K_M + nomic-embed-text-v1.5 Q4_K_M) | 6 passed |
| `npx vitest run` (dashboard) | 9 passed |
| `npx tsc -b` | 0 errors |
| `npx playwright test` (full loop + no horizontal overflow; desktop 1280px and phone 390px) | 4 passed |
| GitHub Actions `ci` (macOS, Windows, Ubuntu Python jobs; Ubuntu real-model e2e; web job) | green on the latest pushes, and e2e green on 3+ consecutive runs |

## 1. A real client works unchanged — ✓ (with two GUI clients left to the author)

- **OpenAI Python SDK:** the e2e suite covers chat, streaming, forced tool calls, `json_schema` structured output and embeddings (768 dimensions). It passes on the M4 and in Ubuntu CI.
- **`llm` CLI** (third-party; Simon Willison) via `extra-openai-models.yaml` pointing at StrixRun, no code changes:
  - The prompt answered.
  - `-o temperature 0` was honoured.
  - Both requests were logged as streamed, with TTFT (56 ms and 884 ms; the second included a cold start) and about 80 tok/s.
- **LangChain `ChatOpenAI`** (third-party):
  - `bind_tools` produced the call `get_weather({'city': 'Lisbon'})`.
  - The tool result was fed back, and the model answered "The weather in Lisbon is 18°C and clear."
  - `.stream()` works.
- **Not run here:** Open WebUI and Continue, which are GUI apps. They're listed for the author's manual pass, and their configuration is documented in the README.

## 2. Full dashboard loop — ✓

These were checked against real models in the in-app browser at 375 px and desktop width:
1. Hugging Face search, then the quant table with fit badges for every quant.
2. A real download of nomic Q2_K from the browser, with live progress, then "Downloaded ✓".
3. Delete through the confirm dialog removed exactly that file.
4. The load sheet with a live estimate: 8k gave 2.1 GB, and 32k × 2 slots gave 3.6 GB against an 8.8 GB Metal limit.
5. The load finished in about 1 s and the sheet closed itself.
6. The Playground streamed token by token: the reply's length grew through 12 steps, at 83 tok/s and 65–83 ms TTFT.
7. The Requests rack showed a new row 27 ms after the response finished, with TTFT and tok/s.
8. The detail sheet's **Replay in playground** reproduced the conversation.
9. A forced crash (`--threads abc`) showed the classified reason and llama-server's output, and the model moved to the "Needs attention" bay.

Playwright covers the same loop on every CI run (fake backend, desktop and phone).

## 3. LAN access works — ✓

These were checked with the server bound to `0.0.0.0` and the key created via the CLI and the dashboard. The LAN address was 192.168.1.55.

| Check | Result |
|---|---|
| `/v1/models` without the key | 401 |
| `/v1/models` with the key | 200 |
| `/admin/ps` without the key | 401 |
| Dashboard shell | public (200) |
| Dashboard opened at the LAN address | shows the key prompt |
| `POST /admin/session` with a wrong key | 401 |
| `POST /admin/session` with the right key | returns an HttpOnly session cookie |
| `/admin/requests/events` streamed with only that cookie | a LAN request arrived live with `client_ip: 192.168.1.55` |
| Cross-site POST carrying the cookie | 403 |
| Rebinding `Host: evil.com` | 403 |
| Browser sign-in with trust-loopback off: prompt, key, session | the live feed then reconnected (`key_id: session`) |

The config was restored to `127.0.0.1` with no key afterwards.

## 4. Platforms — macOS ✓ · Linux CI ✓ · Windows CUDA pending

- **macOS (Metal), real hardware:**
  - **Tests and dashboard:** everything above.
  - **launchd service:** it started StrixRun with models restored and restarted it after `kill -9`. The old children died through the watchdogs and only one llama-server per model was left. Uninstall stopped and removed it.
  - **First run from a clean install:** an isolated `uv tool install` from the git URL with a fresh `STRIX_HOME` auto-installed llama.cpp. `strixrun load` and the README's curl returned a completion. The model came from `extra_scan_dirs` instead of being re-downloaded.
- **Linux (Ubuntu, CPU):** CI runs the unit, integration and real-model e2e tests.
- **Windows:** CI runs the unit and integration tests, including Job Object and watchdog orphan cleanup, a crash mid-stream, and colon-containing aliases. The **CUDA pass on real hardware is pending** (plan step 35); the checklist is in the README.

## Bugs found and fixed during verification

1. About 1.5% of loads failed at random: a urlsafe child API key starting with `-` was parsed as a flag.
2. A process that grabbed the child's port could pass the health check. Readiness now requires `/props` to accept the child's key and report our model path.
3. Responses leaked the model file's absolute path. Children now get `--alias`, and the proxy rewrites `model` to the client's alias.
4. On macOS, psutil `available` understated free memory (3.7 GB reported at 47% free). StrixRun now uses `kern.memorystatus_level`.
5. Folder scans registered models without metadata, so embedding models weren't detected. Scans now parse GGUF headers and recognise the `owner--repo` layout.
6. A stale `index.html` was cached after upgrades. The shell is now served `no-cache` and hashed assets `immutable`.
7. The load sheet's auto-close never fired: every streamed log line restarted the timer.
8. Accessibility:
   - nav buttons had no names on phones
   - form controls were named by their values
   - all Download buttons were identical
9. The Xet download backend ignores exceptions raised in progress callbacks. Downloads now run in a killable worker process.
10. Upstream asset naming changed: `latest` became a version tag, and the macOS build became `.tar.gz`. Pinning and the manifest checks absorbed it.

## Left for the author

- **Windows CUDA manual pass:** the README "Windows CUDA checklist".
- **Open WebUI and Continue** against StrixRun.
- **macOS login check:** after `strixrun service install`, sign out and back in.
- **Release choices:**
  - PyPI publication (`strixrun` is unclaimed)
  - the repo's license
  - whether to make the repo public
