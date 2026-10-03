// Streaming chat completions over fetch (cookie auth), parsed from SSE.
import { requireKey } from "./auth";
import { ApiError } from "./client";

export type ChatDelta = {
  content?: string;
  reasoning?: string;
  usage?: { prompt_tokens: number; completion_tokens: number };
  timings?: { predicted_per_second?: number };
  finish?: string;
  error?: string;
};

/** Parse an SSE byte stream into JSON payloads of `data:` lines. */
export async function* sseEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<unknown> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const event = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      for (const line of event.split("\n")) {
        if (!line.startsWith("data:")) continue;
        const data = line.slice(5).trim();
        if (data === "[DONE]") return;
        try {
          yield JSON.parse(data);
        } catch {
          /* skip malformed */
        }
      }
    }
  }
}

export function toDelta(obj: unknown): ChatDelta {
  const o = obj as Record<string, any>;
  if (o.error) return { error: typeof o.error === "object" ? o.error.message : String(o.error) };
  const choice = o.choices?.[0];
  const d: ChatDelta = {};
  if (choice?.delta?.content) d.content = choice.delta.content;
  if (choice?.delta?.reasoning_content) d.reasoning = choice.delta.reasoning_content;
  if (choice?.finish_reason) d.finish = choice.finish_reason;
  if (o.usage) d.usage = o.usage;
  if (o.timings) d.timings = o.timings;
  return d;
}

export async function* streamChat(body: Record<string, unknown>, signal: AbortSignal, retried = false): AsyncGenerator<ChatDelta> {
  const r = await fetch("/v1/chat/completions", {
    method: "POST",
    headers: { "content-type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ ...body, stream: true, stream_options: { include_usage: true } }),
    signal,
  });
  if (r.status === 401 && !retried) {
    await requireKey();
    yield* streamChat(body, signal, true);
    return;
  }
  if (!r.ok || !r.body) {
    const data = await r.json().catch(() => null);
    throw new ApiError(r.status, data?.error?.code ?? null, data?.error?.message ?? `Request failed (${r.status})`);
  }
  for await (const obj of sseEvents(r.body)) yield toDelta(obj);
}
