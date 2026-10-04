# Modality plan: images and files in, images out

**Goal:** StrixRun accepts images, audio and documents in chat requests from the API and the
playground, for models that can read them. Later, it serves image-generation models through
`/v1/images/generations`.

Two phases. Phase 1 (input) builds on llama.cpp, which StrixRun already runs, and ships now.
Phase 2 (image generation) adds a second engine, so it gets its own design pass first.

---

## Phase 1: multimodal input (built in this change)

### What llama.cpp gives us (b11146)

- `llama-server -m model.gguf --mmproj mmproj.gguf` turns on media input. The projector is a
  separate GGUF that ships next to the model on Hugging Face (`mmproj-F16.gguf`,
  `mmproj-model-f16.gguf`, …).
- `GET /props` reports `modalities: {vision, audio, video}` for the loaded projector.
- `/v1/chat/completions` accepts OpenAI content parts: `image_url` (data URI, http(s) URL, or
  raw base64), `input_audio` (`data` base64), and `video_url`/`input_video`. Anything else
  fails with "unsupported content[].type".
- llama-server fetches http(s) media URLs itself: 10 MB cap, 10 s timeout, no address
  filtering. Requests without a projector fail with
  "image input is not supported - hint: … provide the mmproj".

### Design

**Projectors are part of the model.** `ModelEntry.mmproj` holds the projector path.
- Download: the repo listing reports the repo's projector (preference F16 > BF16 > Q8_0 >
  F32 > anything else named `mmproj*`). A quant download fetches it too, and its size counts
  toward the size and fit badge.
- Scan: a model gets the `mmproj*.gguf` in its own folder. Existing entries pick one up on
  the next rescan (startup), so models already downloaded with a projector start working.
- Models downloaded before this change, without a projector, get **Get vision projector** in
  the load drawer: `POST /admin/models/{id}/projector` starts a projector-only download job.
- Preset `projector: bool = true` controls whether to load it. Turning it off saves memory
  for text-only use. On CPU-only presets the projector stays on the CPU too
  (`--no-mmproj-offload`).
- Memory estimate counts projector weights when the projector is used.

**Capabilities come from the running server.** After `/props` confirms readiness, the
supervisor stores `modalities` in `ModelState`. The admin model view and `/v1/models`
expose them (`input_modalities: ["text", "image", …]`, the same field llama.cpp's own router
uses), and the playground reads them.

**StrixRun normalizes media before forwarding (`media.py`).** Every chat request's content
parts are checked and rewritten before a slot is taken:

| Part | Handling |
| --- | --- |
| `text` | unchanged |
| `image_url` (data URI) | must be `image/*` and under the size cap |
| `image_url` (http/https) | StrixRun downloads it (cap, timeout, `image/*` only, at most 3 redirects) and inlines it as a data URI. Turned off with the `media_fetch_remote` setting. llama-server never makes outbound requests. |
| `input_audio`, `video_url`/`input_video` | size-checked and passed through |
| `file` (`file_data` data URI + `filename`) | images become `image_url`, audio becomes `input_audio`, PDFs become extracted text (pypdf), and text-like files (txt, md, csv, json, code…) become text, each wrapped as `<file name="…">…</file>`. `file_id` is rejected: StrixRun has no Files API. |
| anything else | 400 `unsupported_content` |

Requests with media for a model that lacks that modality get a clear 400
(`modality_not_supported`) that names the fix, before they reach llama-server.

Caps: 20 MB per media item and 64 MB per request body.

**Logs stay small.** The request ring stores a redacted copy of the body: each inlined media
payload is replaced by a short placeholder (`[image/png, 412 KB, not kept]`). Replaying such
a request in the playground drops those attachments and says so.

**Playground.** It has an attach button, paste and drag-and-drop. Images show as thumbnails
in the composer and the conversation, and other files show as chips. Large images are scaled
in the browser to at most 2048 px on the long side before upload. Images are disabled, with
a reason, when the selected model has no vision. Documents work with any chat model because
they become text.

**API page.** It gets a "Vision" snippet for curl, Python and JavaScript.

### Out of scope for Phase 1 (follow-ups)

- Scanned PDFs, which have no text layer, and rendering PDF pages to images for vision
  models. That needs pypdfium2, a binary wheel.
- Office documents (docx/xlsx/pptx) to text.
- `/v1/completions` with `multimodal_data`.
- An OpenAI Files API (`file_id`).
- Thumbnails kept in the request ring for faithful replay.

---

## Phase 2: image generation (plan only)

### Engine: stable-diffusion.cpp `sd-server`

- Same family as llama.cpp (ggml), with prebuilt releases for Metal, CUDA, Vulkan and CPU.
  StrixRun's binary manager pattern (pinned manifest, sha256, smoke test, rollback) carries
  over.
- Runs SD 1.x/2.x/XL/3.5, FLUX, Qwen-Image, Z-Image and Wan (video), from GGUF or
  safetensors.
- `sd-server` already serves **`POST /v1/images/generations`** (JSON: `prompt`, `n`,
  `size`, `output_format`, `output_compression`, returns `data[].b64_json`) and
  **`POST /v1/images/edits`** (multipart). Native parameters such as steps, cfg, sampler and
  seed travel inside the prompt as
  `<sd_cpp_extra_args>{…}</sd_cpp_extra_args>`. There is also an async job API under
  `/sdcpp/v1/` (`img_gen`, `jobs/{id}`, `jobs/{id}/cancel`), which StrixRun should use for
  progress and cancel.
- One model per process, the same shape as llama-server, so the supervisor model holds.

### What changes in StrixRun

1. **Engines.** Add `ModelEntry.engine: "llama" | "sd"` and a second binary manifest
   (`binaries/sd-manifest.json`), installed lazily the first time someone downloads an image
   model. The Backend protocol stays; `LocalProcessBackend` gets a per-engine command
   builder, health check (`GET /sdcpp/v1/capabilities` in place of `/props`) and crash
   patterns.
2. **Multi-file models.** Image models are pipelines: a diffusion model plus a VAE plus one
   or more text encoders (CLIP-L, T5-XXL, Qwen/Llama LLM). The registry gets a
   `components: {diffusion_model, vae, clip_l, t5xxl, llm, …}` map, and downloads become
   "recipes": a curated list per family (FLUX.1-schnell, SDXL-Turbo, Z-Image-Turbo,
   Qwen-Image) naming which repo and file fills each slot. Hugging Face search alone can't
   assemble these, so Discover gets an **Image** tab driven by the recipe list, with fit
   badges from summed component sizes.
3. **Presets.** Add `mode: "image"` with image-specific fields: default size, steps, cfg
   scale, sampler, scheduler, `diffusion_fa`, `offload_to_cpu`, `vae_tiling`. These map to
   sd-server flags, with the same allowlist discipline as `extra_args`.
4. **Memory.** Sum component weights, plus a compute buffer that scales with resolution.
   `offload_to_cpu` changes placement the way the GPU-layers split does for llama.
5. **API.**
   - `POST /v1/images/generations`: StrixRun accepts standard OpenAI fields plus first-class
     `steps`, `cfg_scale`, `seed`, `sampler` and `negative_prompt`, translates them into
     `sd_cpp_extra_args`, and submits through the async API so the client's disconnect
     cancels the job. `response_format: "url"` is served from a short-lived in-memory image
     store at `/v1/images/files/{id}`; `b64_json` is the default.
   - `POST /v1/images/edits` (multipart), proxied after the same validation.
   - The proxy's gate and queue apply unchanged (parallel = 1 for image models). Request
     records gain `endpoint="images"`, `images` (count), `steps`, `width`/`height` and
     `seconds_per_step` in place of tokens per second.
6. **Dashboard.**
   - Playground gets an **Image** mode when an image model is selected: prompt, negative
     prompt, size, steps, cfg, seed and count, a progress bar from the job API, and a gallery
     with download and "use as input for edit".
   - The Requests page shows image thumbnails, which the ring keeps small.
   - The Models page shows the component list.
7. **Chat to image bridge (later).** A tool-call shim lets a chat model in the playground
   call `generate_image`.

### Open questions for Phase 2

- Which families to ship recipes for first? Suggested: Z-Image-Turbo (small, fast, good),
  FLUX.1-schnell (Apache-2.0) and SDXL-Turbo.
- CUDA builds: sd.cpp ships separate CUDA assets. Should StrixRun reuse llama.cpp's CUDA
  runtime extras or download its own?
- Store generated images on disk (opt-in gallery), or keep them in memory only like
  request bodies?
- Video (Wan, LTX) through `/sdcpp/v1/vid_gen`: in scope or not?

### Suggested order

1. sd binary manifest and install, plus `engine` on registry entries. Load a hand-placed
   model, health check.
2. `/v1/images/generations` (sync, b64), metrics, request log.
3. Recipes, the Discover Image tab, multi-file downloads, fit estimate.
4. Playground image mode with progress and cancel.
5. Edits, `response_format: url`, the chat to image tool shim.
