// Fetch wrapper for the Enjro Run admin and /v1 APIs.
import { requireKey } from "./auth";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string | number | null,
    message: string,
    public body: unknown = null,
  ) {
    super(message);
  }
}

type Options = { method?: string; body?: unknown; signal?: AbortSignal };

export async function api<T>(path: string, opts: Options = {}, retried = false): Promise<T> {
  const method = opts.method ?? "GET";
  const init: RequestInit = { method, credentials: "same-origin", signal: opts.signal };
  if (method !== "GET") {
    init.headers = { "content-type": "application/json" };
    init.body = JSON.stringify(opts.body ?? {});
  }
  const r = await fetch(path, init);
  if (r.status === 401 && !retried) {
    await requireKey();
    return api<T>(path, opts, true);
  }
  const text = await r.text();
  const data = text ? safeJson(text) : null;
  if (!r.ok) {
    const err = (data as { error?: { message?: string; code?: string | number } } | null)?.error;
    throw new ApiError(r.status, err?.code ?? null, err?.message ?? `Request failed (${r.status})`, data);
  }
  return data as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/** Subscribe to a server-sent event stream; returns an unsubscribe function. */
export function subscribe(
  path: string,
  handlers: Record<string, (data: unknown) => void>,
  makeSource: (url: string) => EventSource = (url) => new EventSource(url, { withCredentials: true }),
  onStatus?: (connected: boolean) => void,
): () => void {
  let closed = false;
  let source: EventSource | null = null;

  const open = () => {
    source = makeSource(path);
    for (const [event, fn] of Object.entries(handlers)) {
      source.addEventListener(event, (e) => {
        try {
          fn(JSON.parse((e as MessageEvent).data));
        } catch {
          /* ignore malformed events */
        }
      });
    }
    source.onopen = () => onStatus?.(true);
    source.onerror = async () => {
      onStatus?.(false);
      // EventSource can't see status codes: ask the session endpoint whether we're signed out.
      if (closed) return;
      const r = await fetch("/admin/session", { credentials: "same-origin" }).catch(() => null);
      if (r?.status === 401) {
        source?.close();
        await requireKey();
        if (!closed) open();
      }
    };
  };
  open();
  return () => {
    closed = true;
    source?.close();
  };
}
