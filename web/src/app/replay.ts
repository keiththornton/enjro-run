// Hand-off from the Requests page to the Playground ("Replay in playground").
import { useSyncExternalStore } from "react";

export type ChatMessage = { role: "system" | "user" | "assistant" | "tool"; content: string };
export type Replay = { model: string; messages: ChatMessage[]; params: Record<string, number> };

let pending: Replay | null = null;
const listeners = new Set<() => void>();

export function setReplay(r: Replay | null) {
  pending = r;
  listeners.forEach((fn) => fn());
}

/** Read and clear the pending replay. */
export function takeReplay(): Replay | null {
  const r = pending;
  pending = null;
  return r;
}

export function useReplayPending(): boolean {
  return useSyncExternalStore(
    (fn) => {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    () => pending !== null,
  );
}

/** Build a replay from a logged chat request body (OpenAI format). */
export function replayFromRequest(body: unknown, model: string): Replay | null {
  if (!body || typeof body !== "object") return null;
  const b = body as Record<string, unknown>;
  if (!Array.isArray(b.messages)) return null;
  const messages: ChatMessage[] = b.messages
    .filter((m): m is Record<string, unknown> => !!m && typeof m === "object")
    .map((m) => ({
      role: (["system", "user", "assistant", "tool"].includes(String(m.role)) ? m.role : "user") as ChatMessage["role"],
      content: typeof m.content === "string" ? m.content : JSON.stringify(m.content ?? ""),
    }));
  const params: Record<string, number> = {};
  for (const k of ["temperature", "top_p", "max_tokens", "seed"]) {
    if (typeof b[k] === "number") params[k] = b[k] as number;
  }
  return { model, messages, params };
}
