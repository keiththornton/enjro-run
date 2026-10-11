# Enjro Run

An API-first local LLM server. Enjro Run runs GGUF models with [llama.cpp](https://github.com/ggml-org/llama.cpp), serves them through an **OpenAI-compatible API**, and has a light web dashboard for managing models and watching every request.

Enjro Run was called **StrixRun** until 0.2.0. Upgrading keeps your models, settings and API key; see [Upgrading from StrixRun](#upgrading-from-strixrun).

- **OpenAI-compatible.** It supports `/v1/chat/completions` (streaming, tool calls, `json_schema` structured output), `/v1/completions`, `/v1/embeddings` and `/v1/models`. Existing SDKs and tools work unchanged.
- **Observable.** Every request is logged with time to first token, tokens per second, prompt and completion tokens, queue time and status. The dashboard shows them live, and any recent request can be replayed in the playground.
- **Fit before you download.** Hugging Face search shows whether each quantization fits your machine, estimated from the model's header before downloading. Loading shows a live memory estimate.
- **Predictable.** Models load and unload only when you say so, and are restored after a restart. Requests for a model that isn't loaded get a clear error rather than a surprise swap.
- **Pinned llama.cpp.** Enjro Run downloads a tested llama.cpp build for your platform (Metal, CUDA 12/13 or CPU), verifies its checksum, and can roll back.

## Install

### Windows: installer

Download **EnjroRun-Setup-<version>.exe** from the repository's Releases page and run it.

- **No admin rights needed.** It installs for your user only.
- **No Python or Git needed.** The first install downloads a private copy of Python and Enjro Run's packages, which takes about a minute.
- **Start Menu:** **Enjro Run** starts the server, with no console window, and opens the dashboard. **Stop Enjro Run** stops it.
- **Optional extras:** start Enjro Run when you sign in, and add the `enjrorun` command to PATH.
- **Uninstalling:** use *Settings → Apps*. Your models and settings in `%USERPROFILE%\.enjro` are kept; delete that folder to remove them too.
- **SmartScreen:** the installer isn't code-signed, so Windows may say *"Windows protected your PC"*. Choose **More info → Run anyway**.

### Linux and macOS: one command

```bash
gh release download --repo keiththornton/strixrun --pattern install.sh --clobber && bash install.sh
```

That's all: it installs [uv](https://docs.astral.sh/uv/) if you don't have it, downloads the release's prebuilt package (dashboard included, so you don't need Node or Git), checks its SHA-256, installs the `enjrorun` command and starts Enjro Run. It prints the dashboard address when it's done.

- **Sign-in:** the repository is private, so the download uses the [GitHub CLI](https://cli.github.com/) (`gh auth login` once) or a token in `GH_TOKEN`. Once it's public, `curl -fsSL https://github.com/keiththornton/strixrun/releases/latest/download/install.sh | bash` works with no sign-in.
- **Start at login:** add `--service` (a systemd user unit on Linux, a launchd agent on macOS). On a Linux server, also run `sudo loginctl enable-linger $USER` so it keeps running when you log out.
- **A specific version:** `--version v0.2.0`. This also rolls back.
- **From a clone of this repository:** `installer/install.sh` does the same.
- **Uninstall:** `bash install.sh --uninstall`. Your models and settings in `~/.enjro` are kept.

To build from source instead, use `--from-source` (needs Git and Node 20+), or `uv tool install git+ssh://git@github.com/keiththornton/strixrun`. Building without Node stops with an error rather than installing Enjro Run without its dashboard.

On Linux and Windows with an NVIDIA GPU, Enjro Run downloads a CUDA build of llama.cpp, chosen from your driver version and GPUs:

- **CUDA 13:** driver 580 or newer, and every GPU from the RTX 20 series (compute capability 7.5) or later.
- **CUDA 12:** older drivers (Linux 525.60+, Windows 527.41+), or when any GPU is older, such as a GTX 10 series card. CUDA 13 dropped support for those.
- **CPU build:** otherwise.

The CUDA runtime comes with the download, so you only need the NVIDIA driver, not the CUDA toolkit. If you'd rather use your own llama-server build, point `binary_path` at it (see [Configuration](#configuration)).

## Update

Your models, settings and API key are in `~/.enjro` (`%USERPROFILE%\.enjro` on Windows), and updating keeps them. Models that were loaded are loaded again when Enjro Run restarts.

### Windows

Download the newer **EnjroRun-Setup-<version>.exe** and run it. The installer stops Enjro Run, updates it in place and keeps your choices from the first install.

### Linux and macOS

Run the installer again. Without `--version` it installs the newest release:

```bash
gh release download --repo keiththornton/strixrun --pattern install.sh --clobber && bash install.sh
```

It stops Enjro Run (whether it runs as a login service or was started with `enjrorun open`), installs the new version, and starts it again the same way. If the install fails, the previous version stays installed and is started again. Add `--no-start` to leave it stopped.

### Upgrading from StrixRun

StrixRun is Enjro Run's old name. Upgrading moves everything over:

| Before | After |
|---|---|
| `strixrun` command | `enjrorun` |
| `~/.strix/run` (`%USERPROFILE%\.strix\run`) | `~/.enjro/run` (`%USERPROFILE%\.enjro\run`) |
| `STRIX_HOME`, `STRIXRUN_API_KEY`, `STRIXRUN_URL` | `ENJRO_HOME`, `ENJRORUN_API_KEY`, `ENJRORUN_URL` (the old names still work) |
| Login service `StrixRun` / `strixrun.service` / `com.strixrun.serve` | `EnjroRun` / `enjrorun.service` / `com.enjrorun.serve` |
| Metrics `strixrun_*` | `enjrorun_*` |

- **Windows:** run **EnjroRun-Setup-<version>.exe**. It replaces the StrixRun install in place (one entry in *Apps*), removes StrixRun's Start Menu shortcuts and PATH entry, moves your data folder and carries over the sign-in task.
- **Linux and macOS:** run `install.sh` as above. It stops StrixRun, uninstalls the `strixrun` command, installs Enjro Run and moves your data and login service over.
- **By hand:**

  ```bash
  strixrun stop
  uv tool uninstall strixrun
  bash install.sh --no-start   # or: uv tool install git+ssh://git@github.com/keiththornton/strixrun
  enjrorun migrate     # moves ~/.strix/run and replaces a StrixRun login service
  enjrorun open
  ```

The data folder is moved in one step, so nothing is copied, and absolute paths inside your settings are updated. If it can't be moved yet (an old StrixRun is still running, or a file in it is open), Enjro Run keeps using `~/.strix/run` and tries again the next time it starts. A custom `STRIX_HOME` is used where it is. The dashboard asks for your API key again once, because the sign-in cookie was renamed; the key itself doesn't change.

### By hand

If you installed with `uv tool install` from Git, update the same way, then restart:

```bash
enjrorun stop
uv tool install --force --reinstall-package enjrorun git+ssh://git@github.com/keiththornton/strixrun
enjrorun open
```

## Quickstart

```bash
enjrorun serve
```

On first start Enjro Run downloads its pinned llama.cpp build (about 12 MB on a Mac; about 650 MB on Windows with CUDA, and 590–760 MB on Linux with CUDA, including the CUDA runtime). Then:

1. Open **http://127.0.0.1:8790**.
2. Choose **Find on Hugging Face** and search (e.g. `qwen2.5 instruct`), or browse **Discover** for trending, most downloaded, most liked and new GGUF models (only ones Enjro Run can run; large ones are hidden by default). Expand a repo and download a quant marked **fits**.
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

**Open WebUI.** In its admin Connections settings, add an OpenAI API connection to `http://<host>:8790/v1`, with your Enjro Run key if one is set.

**Continue** (`config.yaml`):

```yaml
models:
  - name: Qwen 2.5 1.5B (Enjro Run)
    provider: openai
    model: qwen2.5-1.5b-instruct:q4_k_m
    apiBase: http://127.0.0.1:8790/v1
    apiKey: unused-on-this-computer
```

**Aliases.** Give a model extra names on the Models page (pencil icon), e.g. `gpt-4o-mini`. Clients that hard-code an OpenAI model name will then work unchanged. Responses always report the name the client asked for.

## Use it from other devices

By default Enjro Run listens only on this computer. To share it on your network:

```bash
enjrorun key set          # prints a new API key once; copy it
enjrorun config set host 0.0.0.0
enjrorun serve
```

- **API clients** on other devices send the key as `Authorization: Bearer <key>` (or `x-api-key`).
- **The dashboard**, opened from another device, asks for the key once and keeps you signed in with a session cookie.
- **Safety check:** Enjro Run refuses to listen beyond this computer without a key.
- **Same-computer requests** skip the key by default. If you put Enjro Run behind a reverse proxy or tunnel on the same computer (nginx, Caddy, `tailscale serve`, cloudflared), every request appears to come from this computer. In that case turn this off, so the key is always required:

```bash
enjrorun config set trust_loopback false
```

## Use it from the internet

To reach Enjro Run at a public HTTPS address, even on a home connection with a changing IP address, use [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/). `cloudflared` runs next to Enjro Run and connects out to Cloudflare. You don't need port forwarding or dynamic DNS, and Cloudflare provides the HTTPS certificate. You need a domain on Cloudflare (a free account is enough).

1. Set a key and tell Enjro Run its public name. Leave `host` at `127.0.0.1`, because the tunnel connects locally:

   ```bash
   enjrorun key set
   enjrorun config set public_hosts enjro.example.com
   enjrorun config set trust_loopback false   # recommended; see below
   ```

   Restart Enjro Run.

2. Install `cloudflared` (on Windows: `winget install --id Cloudflare.cloudflared`), then create and route a tunnel:

   ```bash
   cloudflared tunnel login
   cloudflared tunnel create enjrorun
   cloudflared tunnel route dns enjrorun enjro.example.com
   ```

3. Point the tunnel at Enjro Run in `~/.cloudflared/config.yml` (on Windows, `%USERPROFILE%\.cloudflared\config.yml`):

   ```yaml
   tunnel: enjrorun
   credentials-file: <path printed by "tunnel create">
   ingress:
     - hostname: enjro.example.com
       service: http://127.0.0.1:8790
     - service: http_status:404
   ```

   Test it with `cloudflared tunnel run enjrorun`. To keep it running, install it as a service: `cloudflared service install` (from an admin prompt on Windows).

4. Use `https://enjro.example.com/v1` as the base URL, with your key. The dashboard at `https://enjro.example.com` asks for the key once.

How it stays safe:

- **The key is always required at a public name.** Requests for a `public_hosts` name never get the same-computer exemption, even though the tunnel delivers them from this computer. Setting `trust_loopback false` as well covers requests that reach the tunnel some other way.
- **No key, no start.** Enjro Run refuses to start with `public_hosts` set and no key.
- **HTTPS-only session cookie.** The dashboard's session cookie is marked `Secure` at a public name, so the browser only sends it over HTTPS.
- **Only the `public_hosts` setting changes this.** Like `host`, it can only be changed with `enjrorun config set`, never over the HTTP API.
- **Optional login in front.** API keys are 256-bit random values, so guessing one isn't practical. If you want a login page in front of the dashboard as well, add a [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/applications/) policy. Give API clients a service token or a separate hostname so they aren't blocked.
- **Long requests can time out.** Cloudflare gives up on a response that doesn't start within 100 seconds (error 524). For long generations, use streaming (`"stream": true`).

## Command line

| Command | What it does |
|---|---|
| `enjrorun serve [--port N]` | Run the server and dashboard |
| `enjrorun open` / `enjrorun stop` | Start the server in the background if needed and open the dashboard / stop it |
| `enjrorun load <model> [--ctx N --parallel N --gpu-layers N --gpus 1\|0,1\|all --yes --no-wait]` | Load a model and wait until it's serving; exits non-zero with the reason if it fails |
| `enjrorun unload <model>` | Unload a model |
| `enjrorun ps` | Show loaded models: context, slots, queue, memory, uptime |
| `enjrorun key set` / `rotate` / `clear` | Manage the API key (works while the server is stopped) |
| `enjrorun config set <field> <value>` / `config show` | Change config, including file-only fields (see below) |
| `enjrorun service install` / `uninstall` | Start Enjro Run at login (launchd on macOS, Task Scheduler on Windows, systemd user unit on Linux) |
| `enjrorun migrate` | Move StrixRun's data folder and login service over (see [Upgrading from StrixRun](#upgrading-from-strixrun)) |

`load`, `unload` and `ps` talk to the running server. Use `--key` or `ENJRORUN_API_KEY` if it requires a key, and `--url` or `ENJRORUN_URL` for a different address.

## Configuration

Everything lives in `~/.enjro/run/` (set `ENJRO_HOME` to move it):

| Path | Contents |
|---|---|
| `config.json` | Settings (mode 0600) |
| `models/` | Downloaded models, in `owner--repo/` folders |
| `models.json` | Model registry: IDs, aliases, metadata |
| `presets/` | Last-used load settings per model |
| `bin/` | Installed llama.cpp builds |
| `enjrorun.db` | Request log: metadata only |
| `logs/` | Service logs |

**File-only fields.** `host`, `public_hosts`, `binary_path`, `models_dirs` and `extra_scan_dirs` can only be changed with `enjrorun config set`, never over the HTTP API. They name programs or folders, so a client holding the API key can't repoint them.

Useful settings:

- `extra_scan_dirs`: read GGUF files from other folders, e.g. LM Studio's `~/.lmstudio/models`. Enjro Run never writes to or deletes from these folders.
- `binary_path`: use your own `llama-server` build instead of the pinned one.
- `restore_loaded` (default `true`): reload models that were loaded when Enjro Run stopped.
- `log_retention_days` (default 30) and `log_body_ring_size` (default 50; `0` keeps no bodies).

## Privacy

- **Request log:** only metadata goes to disk: model, timings, token counts, status, client IP and which credential was used. Rows are kept for `log_retention_days`.
- **Prompts and responses:** kept only in memory, for the last `log_body_ring_size` requests, so you can inspect and replay them. They're gone on restart. Set the size to `0` to keep none.
- **Hugging Face token:** kept in huggingface_hub's own token file (or `HF_TOKEN`), never in Enjro Run's config. The API only ever reports whether it's set.
- **Outbound connections:** Enjro Run makes none except to Hugging Face (search, downloads) and GitHub (llama.cpp downloads), and only when you use those features.

## Troubleshooting

- **"model is not loaded" (409):** load it on the Models page or with `enjrorun load <model>`. Loading is always manual.
- **The memory estimate says tight or too large:**
  - Lower the context per request or the parallel slots, or pick a smaller quant.
  - On Apple silicon, the GPU can only use about two-thirds of RAM by default, and the estimate accounts for that.
  - It's an estimate: Enjro Run warns, and you can still choose **Load anyway**.
- **Replies are slow, even in a new conversation (NVIDIA):** part of the model is running on the CPU.
  - Check the model's strip: `GPU 20/33 · rest on CPU` means 13 layers didn't fit in VRAM, and every token waits on them.
  - In the load sheet, **Fit on GPU** picks the largest context that keeps every layer on the GPU, using an 8-bit (q8_0) KV cache if that buys more room.
  - Otherwise lower the context or pick a smaller quant.
  - Parallel requests share one context (llama.cpp's unified KV cache), so raising them doesn't cost memory.
- **More than one NVIDIA GPU:** by default llama.cpp splits the model's layers across all of them, so the estimate adds up their free VRAM (less about 0.5 GB of buffers per extra GPU).
  - To use only some of them, pick them under **GPUs** in the load sheet (or `enjrorun load <model> --gpus 1`). The estimate and **Fit on GPU** then count only those GPUs.
  - GPU numbers match the Settings page and `nvidia-smi`. Enjro Run orders CUDA by PCI bus, so llama.cpp's `CUDA0` in the logs is the same GPU 0. Hover the model's `GPU x/y` badge to see which GPUs it loaded on.
- **Requests fail with a context error:** raise the context per request in the load sheet. Parallel requests share it, so several long conversations at once can fill it sooner.
- **A gated model won't download:** accept its license on huggingface.co, then add a Hugging Face token in Settings.
- **Hugging Face rate limits:** add a token in Settings, or retry in a few minutes.
- **A model shows "Needs attention":** it crashed. The reason is on the strip, and the load sheet shows llama-server's output. Typical causes are out of memory, an architecture this llama.cpp build doesn't support (update the build in Settings), or a rejected extra argument.
- **The first load after installing takes ~15 seconds:** on macOS that's the system checking the newly downloaded llama-server the first time it runs. Later loads take about a second.
- **Settings shows "Better build available":** Enjro Run was installed before it could detect your NVIDIA GPU, or before Linux had CUDA builds, so it's still on the CPU build. Unload your models, then choose **Install** on that row to switch to the CUDA build. Updating Enjro Run doesn't switch builds by itself, because the CUDA download is large.
- **The page at `/` says the dashboard is missing:** that install was built without Node. Reinstall with `install.sh` (Linux, macOS) or the Windows installer; both include the dashboard.

## Tested platforms

| Platform | Build | Status |
|---|---|---|
| macOS 27, Apple M4, 16 GB | `macos-arm64` (Metal) | **Verified.** Unit, integration and real-model e2e tests; dashboard loop; LAN access with a key; launchd service |
| Ubuntu (GitHub Actions) | `linux-cpu-x64` | **Verified in CI.** Unit, integration and real-model e2e tests (CPU) |
| Windows (GitHub Actions) | (CPU, fake models) | **Verified in CI.** Unit and integration tests, including Job Object orphan cleanup |
| Windows, NVIDIA GPU | `win-cuda-12/13-x64` | **Pending a manual pass** (see below) |
| Linux, NVIDIA GPU | `linux-cuda-12/13-x64` | **Pending a manual pass** (same checklist). The install was checked on Ubuntu without a GPU: checksums verify, llama-server runs, and the bundled CUDA libraries resolve from beside it |

### NVIDIA GPU checklist (Windows and Linux)

1. Install it (see [Install](#install)) and start it. The CUDA build and runtime download on their own (the build is chosen from your driver version).
2. Download and load a 7B model fully offloaded. In the load sheet's estimate, the limit should read "free VRAM".
3. Run the e2e suite against it: `uv run pytest -m e2e tests/e2e`.
4. End the Enjro Run process (Task Manager on Windows, `kill -9` on Linux). No `llama-server` should remain.
5. Give a model an alias containing `:` and reload it: its preset saves and loads.
6. `enjrorun service install`, sign out and back in: Enjro Run is up, with models restored.
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
