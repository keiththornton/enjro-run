// Key prompt state: any 401 asks for the key once, then waiting calls retry.
import { useSyncExternalStore } from "react";

type Waiter = () => void;

let needKey = false;
let waiters: Waiter[] = [];
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((fn) => fn());
}

export function useNeedKey(): boolean {
  return useSyncExternalStore(
    (fn) => {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    () => needKey,
  );
}

/** Resolves after the user signs in with a valid key. */
export function requireKey(): Promise<void> {
  needKey = true;
  emit();
  return new Promise((resolve) => waiters.push(resolve));
}

/** Exchange the key for a session cookie. Returns an error message, or null on success. */
export async function signIn(key: string): Promise<string | null> {
  const r = await fetch("/admin/session", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ key }),
    credentials: "same-origin",
  });
  if (!r.ok) return "That key isn't valid. Check it and try again.";
  needKey = false;
  const pending = waiters;
  waiters = [];
  emit();
  pending.forEach((fn) => fn());
  return null;
}

export function resetAuthForTests() {
  needKey = false;
  waiters = [];
}
