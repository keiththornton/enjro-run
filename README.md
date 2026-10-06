# StrixRun

An API-first local LLM server. StrixRun runs GGUF models with [llama.cpp](https://github.com/ggml-org/llama.cpp), serves them through an **OpenAI-compatible API**, and has a light web dashboard for managing models and watching every request.

It's a sister app to StrixCode and Strix Data, and shares their look.

- **OpenAI-compatible.** It supports `/v1/chat/completions` (streaming, tool calls, `json_schema` structured output), `/v1/completions`, `/v1/embeddings` and `/v1/models`. Existing SDKs and tools work unchanged.
- **Observable.** Every request is logged with time to first token, tokens per second, prompt and completion tokens, queue time and status. The dashboard shows them live, and any recent request can be replayed in the playground.
- **Fit before you download.** Hugging Face search shows whether each quantization fits your machine, estimated from the model's header before downloading. Loading shows a live memory estimate.
- **Predictable.** Models load and unload only when you say so, and are restored after a restart. Requests for a model that isn't loaded get a clear error rather than a surprise swap.
- **Pinned llama.cpp.** StrixRun downloads a tested llama.cpp build for your platform (Metal, CUDA 12/13 or CPU), verifies its checksum, and can roll back.

## Install

### Windows: installer

Download **StrixRun-Setup-<version>.exe** from the repository's Releases page and run it.

- **No admin rights needed.** It installs for your user only.
- **No Python or Git needed.** The first install downloads a private copy of Python and StrixRun's packages, which takes about a minute.
- **Start Menu:** **StrixRun** starts the server, with no console window, and opens the dashboard. **Stop StrixRun** stops it.
- **Optional extras:** start StrixRun when you sign in, and add the `strixrun` command to PATH.
- **Uninstalling:** use *Settings → Apps*. Your models and settings in `%USERPROFILE%\.strix` are kept; delete that folder to remove them too.
- **SmartScreen:** the installer isn't code-signed, so Windows may say *"Windows protected your PC"*. Choose **More info → Run anyway**.

### macOS, Linux, or Windows from the command line

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). Building from source also needs Node 20+ for the dashboard.

```bash
uv tool install git+ssh://git@github.com/keiththornton/strixrun
```

That works while the repository is private, using your GitHub SSH key. Once it's public, `git+https://github.com/keiththornton/strixrun` works too.

On Linux and Windows with an NVIDIA GPU, StrixRun downloads a CUDA build of llama.cpp, chosen from your driver version and GPUs:

- **CUDA 13:** driver 580 or newer, and every GPU from the RTX 20 series (compute capability 7.5) or later.
- **CUDA 12:** older drivers (Linux 525.60+, Windows 527.41+), or when any GPU is older, such as a GTX 10 series card. CUDA 13 dropped support for those.
- **CPU build:** otherwise.

The CUDA runtime comes with the download, so you only need the NVIDIA driver, not the CUDA toolkit. If you'd rather use your own llama-server build, point `binary_path` at it (see [Configuration](#configuration)).

## Update

Your models, settings and API key are in `~/.strix` (`%USERPROFILE%\.strix` on Windows), and updating keeps them. Models that were loaded are loaded again when StrixRun restarts.

### Windows

Download the newer **StrixRun-Setup-<version>.exe** and run it. The installer stops StrixRun, updates it in place and keeps your choices from the first install.

### Linux

Run the updater script from a clone of this repository:

```bash
installer/linux/update-strixrun.sh             # latest main
installer/linux/update-strixrun.sh --release   # newest release tag
installer/linux/update-strixrun.sh --ref v0.1.8   # a specific tag, branch or commit (also rolls back)
```

It:

- stops StrixRun, whether it runs as the `strixrun service` user unit or was started with `strixrun open`;
- reinstalls it with `uv tool install`, rebuilding it even when the version number hasn't changed;
- starts it again the same way.

If the update fails, the previous version stays installed and is started again. If StrixRun wasn't running, it stays stopped. Add `--no-restart` to leave it stopped after updating.

The script needs `uv`, `git`, and Node 20+ (the dashboard is built during the install). It stops before changing anything if one is missing, because without Node StrixRun would install with no dashboard.

To install from somewhere else, use `--source` or set `STRIXRUN_SOURCE`, e.g. `--source git+https://github.com/keiththornton/strixrun` once the repository is public. Run it without a previous install to install StrixRun for the first time.

### macOS, or anywhere without the script

Stop StrixRun, reinstall it, then start it again:

```bash
strixrun stop
uv tool install --force --reinstall-package strixrun git+ssh://git@github.com/keiththornton/strixrun
strixrun open
```

Add `@v0.1.9` (or any tag, branch or commit) to the end of the URL to install that version. If StrixRun runs as a service, restart that instead of using `strixrun open`: `launchctl kickstart -k gui/$(id -u)/com.strixrun.serve` on macOS, or `systemctl --user restart strixrun` on Linux.

## Quickstart

```bash
strixrun serve
```

On first start StrixRun downloads its pinned llama.cpp build (about 12 MB on a Mac; about 650 MB on Windows with CUDA, and 590–760 MB on Linux with CUDA, including the CUDA runtime). Then:

1. Open **http://127.0.0.1:8790**.
2. Choose **Find on Hugging Face** and search (e.g. `qwen2.5 instruct`), or browse **Discover** for trending, most downloaded, most liked and new GGUF models (only ones StrixRun can run; large ones are hidden by default). Expand a repo and download a quant marked **fits**.
3. Choose **Load**, check the memory estimate, and choose **Load** again.
4. Try it in the **Playground**, or call the API:

```bash
curl http://127.0.0.1:8790/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model": "qwen2.5-1.5b-instruct:q4_k_m", "messages": [{"role": "user", "content": "Hello!"}]}'
```

The `model` field is the model's **alias**, shown on the Models page and listed by `GET /v1/models`. `GET /v1/models?all=1` also lists downloaded models that aren't loaded.

## Use it from your tools

The dashboard's **API** page shows this server's real base URL, its loaded model names and key status, copy-ready code (curl, Python, JavaScript: chat, streaming, tool calls, JSON output, embeddings), setup for Open WebUI, Continue, LangChain and `llm`, plus the endpoint and error reference. The essentials:

**OpenAI Python SDK**

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8790/v1", api_key="unused-on-this-computer")
reply = client.chat.completions.create(
    model="qwen2.5-1.5b-instruct:q4_k_m",
    messages=[{"role": "user", "content": "Write a haiku about owls."}],
)
print(reply.choices[0].message.content)
```

**OpenAI JavaScript SDK**

```js
import OpenAI from "openai";
const client = new OpenAI({ baseURL: "http://127.0.0.1:8790/v1", apiKey: "unused-on-this-computer" });
```

**Open WebUI.** In its admin Connections settings, add an OpenAI API connection to `http://<host>:8790/v1`, with your StrixRun key if one is set.

**Continue** (`config.yaml`):

```yaml
models:
  - name: Qwen 2.5 1.5B (StrixRun)
    provider: openai
    model: qwen2.5-1.5b-instruct:q4_k_m
    apiBase: http://127.0.0.1:8790/v1
    apiKey: unused-on-this-computer
```

**Aliases.** Give a model extra names on the Models page (pencil icon), e.g. `gpt-4o-mini`. Clients that hard-code an OpenAI model name will then work unchanged. Responses always report the name the client asked for.

## Use it from other devices

By default StrixRun listens only on this computer. To share it on your network:

```bash
strixrun key set          # prints a new API key once; copy it
strixrun config set host 0.0.0.0
strixrun serve
```

- **API clients** on other devices send the key as `Authorization: Bearer <key>` (or `x-api-key`).
- **The dashboard**, opened from another device, asks for the key once and keeps you signed in with a session cookie.
- **Safety check:** StrixRun refuses to listen beyond this computer without a key.
- **Same-computer requests** skip the key by default. If you put StrixRun behind a reverse proxy or tunnel on the same computer (nginx, Caddy, `tailscale serve`, cloudflared), every request appears to come from this computer. In that case turn this off, so the key is always required:

```bash
strixrun config set trust_loopback false
```

## Use it from the internet

To reach StrixRun at a public HTTPS address, even on a home connection with a changing IP address, use [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/). `cloudflared` runs next to StrixRun and connects out to Cloudflare. You don't need port forwarding or dynamic DNS, and Cloudflare provides the HTTPS certificate. You need a domain on Cloudflare (a free account is enough).

1. Set a key and tell StrixRun its public name. Leave `host` at `127.0.0.1`, because the tunnel connects locally:

   ```bash
   strixrun key set
   strixrun config set public_hosts strix.example.com
   strixrun config set trust_loopback false   # recommended; see below
   ```

   Restart StrixRun.

2. Install `cloudflared` (on Windows: `winget install --id Cloudflare.cloudflared`), then create and route a tunnel:

   ```bash
   cloudflared tunnel login
   cloudflared tunnel create strixrun
   cloudflared tunnel route dns strixrun strix.example.com
   ```

3. Point the tunnel at StrixRun in `~/.cloudflared/config.yml` (on Windows, `%USERPROFILE%\.cloudflared\config.yml`):

   ```yaml
   tunnel: strixrun
   credentials-file: <path printed by "tunnel create">
   ingress:
     - hostname: strix.example.com
       service: http://127.0.0.1:8790
     - service: http_status:404
   ```

   Test it with `cloudflared tunnel run strixrun`. To keep it running, install it as a service: `cloudflared service install` (from an admin prompt on Windows).

4. Use `https://strix.example.com/v1` as the base URL, with your key. The dashboard at `https://strix.example.com` asks for the key once.

How it stays safe:

- **The key is always required at a public name.** Requests for a `public_hosts` name never get the same-computer exemption, even though the tunnel delivers them from this computer. Setting `trust_loopback false` as well covers requests that reach the tunnel some other way.
- **No key, no start.** StrixRun refuses to start with `public_hosts` set and no key.
- **HTTPS-only session cookie.** The dashboard's session cookie is marked `Secure` at a public name, so the browser only sends it over HTTPS.
- **Only the `public_hosts` setting changes this.** Like `host`, it can only be changed with `strixrun config set`, never over the HTTP API.
- **Optional login in front.** API keys are 256-bit random values, so guessing one isn't practical. If you want a login page in front of the dashboard as well, add a [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/applications/) policy. Give API clients a service token or a separate hostname so they aren't blocked.
- **Long requests can time out.** Cloudflare gives up on a response that doesn't start within 100 seconds (error 524). For long generations, use streaming (`"stream": true`).

## Command line

| Command | What it does |
|---|---|
| `strixrun serve [--port N]` | Run the server and dashboard |
| `strixrun open` / `strixrun stop` | Start the server in the background if needed and open the dashboard / stop it |
| `strixrun load <model> [--ctx N --parallel N --gpu-layers N --gpus 1\|0,1\|all --yes --no-wait]` | Load a model and wait until it's serving; exits non-zero with the reason if it fails |
| `strixrun unload <model>` | Unload a model |
| `strixrun ps` | Show loaded models: context, slots, queue, memory, uptime |
| `strixrun key set` / `rotate` / `clear` | Manage the API key (works while the server is stopped) |
| `strixrun config set <field> <value>` / `config show` | Change config, including file-only fields (see below) |
| `strixrun service install` / `uninstall` | Start StrixRun at login (launchd on macOS, Task Scheduler on Windows, systemd user unit on Linux) |

`load`, `unload` and `ps` talk to the running server. Use `--key` or `STRIXRUN_API_KEY` if it requires a key, and `--url` or `STRIXRUN_URL` for a different address.

## Configuration

Everything lives in `~/.strix/run/` (set `STRIX_HOME` to move it):

| Path | Contents |
|---|---|
| `config.json` | Settings (mode 0600) |
| `models/` | Downloaded models, in `owner--repo/` folders |
| `models.json` | Model registry: IDs, aliases, metadata |
| `presets/` | Last-used load settings per model |
| `bin/` | Installed llama.cpp builds |
| `strixrun.db` | Request log: metadata only |
| `logs/` | Service logs |

**File-only fields.** `host`, `public_hosts`, `binary_path`, `models_dirs` and `extra_scan_dirs` can only be changed with `strixrun config set`, never over the HTTP API. They name programs or folders, so a client holding the API key can't repoint them.

Useful settings:

- `extra_scan_dirs`: read GGUF files from other folders, e.g. LM Studio's `~/.lmstudio/models`. StrixRun never writes to or deletes from these folders.
- `binary_path`: use your own `llama-server` build instead of the pinned one.
- `restore_loaded` (default `true`): reload models that were loaded when StrixRun stopped.
- `log_retention_days` (default 30) and `log_body_ring_size` (default 50; `0` keeps no bodies).

## Privacy

- **Request log:** only metadata goes to disk: model, timings, token counts, status, client IP and which credential was used. Rows are kept for `log_retention_days`.
- **Prompts and responses:** kept only in memory, for the last `log_body_ring_size` requests, so you can inspect and replay them. They're gone on restart. Set the size to `0` to keep none.
- **Hugging Face token:** kept in huggingface_hub's own token file (or `HF_TOKEN`), never in StrixRun's config. The API only ever reports whether it's set.
- **Outbound connections:** StrixRun makes none except to Hugging Face (search, downloads) and GitHub (llama.cpp downloads), and only when you use those features.

## Troubleshooting

- **"model is not loaded" (409):** load it on the Models page or with `strixrun load <model>`. Loading is always manual.
- **The memory estimate says tight or too large:**
  - Lower the context per request or the parallel slots, or pick a smaller quant.
  - On Apple silicon, the GPU can only use about two-thirds of RAM by default, and the estimate accounts for that.
  - It's an estimate: StrixRun warns, and you can still choose **Load anyway**.
- **Replies are slow, even in a new conversation (NVIDIA):** part of the model is running on the CPU.
  - Check the model's strip: `GPU 20/33 · rest on CPU` means 13 layers didn't fit in VRAM, and every token waits on them.
  - In the load sheet, **Fit on GPU** picks the largest context that keeps every layer on the GPU, using an 8-bit (q8_0) KV cache if that buys more room.
  - Otherwise lower the context or pick a smaller quant.
  - Parallel requests share one context (llama.cpp's unified KV cache), so raising them doesn't cost memory.
- **More than one NVIDIA GPU:** by default llama.cpp splits the model's layers across all of them, so the estimate adds up their free VRAM (less about 0.5 GB of buffers per extra GPU).
  - To use only some of them, pick them under **GPUs** in the load sheet (or `strixrun load <model> --gpus 1`). The estimate and **Fit on GPU** then count only those GPUs.
  - GPU numbers match the Settings page and `nvidia-smi`. StrixRun orders CUDA by PCI bus, so llama.cpp's `CUDA0` in the logs is the same GPU 0. Hover the model's `GPU x/y` badge to see which GPUs it loaded on.
- **Requests fail with a context error:** raise the context per request in the load sheet. Parallel requests share it, so several long conversations at once can fill it sooner.
- **A gated model won't download:** accept its license on huggingface.co, then add a Hugging Face token in Settings.
- **Hugging Face rate limits:** add a token in Settings, or retry in a few minutes.
- **A model shows "Needs attention":** it crashed. The reason is on the strip, and the load sheet shows llama-server's output. Typical causes are out of memory, an architecture this llama.cpp build doesn't support (update the build in Settings), or a rejected extra argument.
- **The first load after installing takes ~15 seconds:** on macOS that's the system checking the newly downloaded llama-server the first time it runs. Later loads take about a second.
- **Settings shows "Better build available":** StrixRun was installed before it could detect your NVIDIA GPU, or before Linux had CUDA builds, so it's still on the CPU build. Unload your models, then choose **Install** on that row to switch to the CUDA build. Updating StrixRun doesn't switch builds by itself, because the CUDA download is large.
- **The dashboard says the dashboard isn't built:** you installed from source without Node. Run `npm ci && npm run build` in `web/`.

## Tested platforms

| Platform | Build | Status |
|---|---|---|
| macOS 27, Apple M4, 16 GB | `macos-arm64` (Metal) | **Verified.** Unit, integration and real-model e2e tests; dashboard loop; LAN access with a key; launchd service |
| Ubuntu (GitHub Actions) | `linux-cpu-x64` | **Verified in CI.** Unit, integration and real-model e2e tests (CPU) |
| Windows (GitHub Actions) | (CPU, fake models) | **Verified in CI.** Unit and integration tests, including Job Object orphan cleanup |
| Windows, NVIDIA GPU | `win-cuda-12/13-x64` | **Pending a manual pass** (see below) |
| Linux, NVIDIA GPU | `linux-cuda-12/13-x64` | **Pending a manual pass** (same checklist). The install was checked on Ubuntu without a GPU: checksums verify, llama-server runs, and the bundled CUDA libraries resolve from beside it |

### NVIDIA GPU checklist (Windows and Linux)

1. `uv tool install git+ssh://git@github.com/keiththornton/strixrun`, then `strixrun serve`. The CUDA build and runtime download on their own (the build is chosen from your driver version).
2. Download and load a 7B model fully offloaded. In the load sheet's estimate, the limit should read "free VRAM".
3. Run the e2e suite against it: `uv run pytest -m e2e tests/e2e`.
4. End the StrixRun process (Task Manager on Windows, `kill -9` on Linux). No `llama-server` should remain.
5. Give a model an alias containing `:` and reload it: its preset saves and loads.
6. `strixrun service install`, sign out and back in: StrixRun is up, with models restored.
7. With two GPUs, load a model on each GPU alone and on both (**GPUs** in the load sheet). `nvidia-smi` should show memory used only on the chosen GPUs.

## Development

```bash
uv sync                            # Python deps
uv run pytest                      # unit + integration tests (fake llama-server)
uv run pytest -m e2e tests/e2e     # real llama.cpp + models (downloads ~1.2 GB once)
cd web && npm ci && npm test       # dashboard unit tests
npm run build && npx playwright test   # dashboard end-to-end (fake backend)
npm run dev                        # dashboard dev server, proxied to :8790
```

The design and implementation plan are in `.brainstorm/design.md` and `.brainstorm/plan.md`.
