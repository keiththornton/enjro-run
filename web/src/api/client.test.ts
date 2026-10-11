import { afterEach, describe, expect, it, vi } from "vitest";
import { resetAuthForTests, signIn, useNeedKey } from "./auth";
import { ApiError, api, subscribe } from "./client";

function respond(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status }));
}

afterEach(() => {
  vi.restoreAllMocks();
  resetAuthForTests();
});

describe("api", () => {
  it("sends JSON for writes and parses responses", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(() => respond(200, { ok: 1 }));
    expect(await api("/admin/x", { method: "POST", body: { a: 1 } })).toEqual({ ok: 1 });
    const [, init] = fetchMock.mock.calls[0];
    expect(init?.headers).toEqual({ "content-type": "application/json" });
    expect(init?.body).toBe('{"a":1}');
  });

  it("raises OpenAI-shaped errors as ApiError", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      respond(409, { error: { message: "unload first", code: "conflict" } }),
    );
    await expect(api("/admin/x")).rejects.toMatchObject({ status: 409, code: "conflict", message: "unload first" });
    await expect(api("/admin/x")).rejects.toBeInstanceOf(ApiError);
  });

  it("on 401 waits for sign-in, then retries once", async () => {
    let calls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url === "/admin/session") return respond(200, { authenticated: true });
      calls += 1;
      return calls === 1 ? respond(401, { error: { message: "key" } }) : respond(200, { data: [1] });
    });
    const pending = api<{ data: number[] }>("/admin/models");
    await vi.waitFor(() => expect(calls).toBe(1));
    expect(await signIn("sk-enjro-good")).toBeNull();
    expect(await pending).toEqual({ data: [1] });
    expect(calls).toBe(2);
  });

  it("reports a bad key without signing in", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => respond(401, { error: { message: "nope" } }));
    expect(await signIn("wrong")).toMatch(/isn't valid/);
    expect(typeof useNeedKey).toBe("function");
  });
});

describe("subscribe", () => {
  it("dispatches named events with parsed JSON and closes", () => {
    const listeners: Record<string, (e: MessageEvent) => void> = {};
    const fake = {
      addEventListener: (name: string, fn: (e: MessageEvent) => void) => (listeners[name] = fn),
      close: vi.fn(),
      onerror: null,
    } as unknown as EventSource;
    const seen: unknown[] = [];
    const unsubscribe = subscribe("/admin/models/events", { "model.state": (d) => seen.push(d) }, () => fake);
    listeners["model.state"](new MessageEvent("model.state", { data: '{"id":"m","status":"ready"}' }));
    listeners["model.state"](new MessageEvent("model.state", { data: "not json" }));
    expect(seen).toEqual([{ id: "m", status: "ready" }]);
    unsubscribe();
    expect(fake.close).toHaveBeenCalled();
  });
});
