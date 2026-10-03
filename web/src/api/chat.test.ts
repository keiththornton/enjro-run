import { describe, expect, it } from "vitest";
import { sseEvents, toDelta } from "./chat";

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream({
    start(c) {
      chunks.forEach((s) => c.enqueue(enc.encode(s)));
      c.close();
    },
  });
}

async function collect(chunks: string[]) {
  const out: unknown[] = [];
  for await (const e of sseEvents(streamOf(chunks))) out.push(e);
  return out;
}

describe("sseEvents", () => {
  it("parses events split across arbitrary chunk boundaries", async () => {
    const text = 'data: {"a":1}\n\ndata: {"b":2}\n\ndata: [DONE]\n\n';
    const pieces = text.match(/.{1,5}/gs)!;
    expect(await collect(pieces)).toEqual([{ a: 1 }, { b: 2 }]);
  });

  it("tolerates CRLF, comments and junk", async () => {
    expect(await collect([": ping\r\n\r\ndata: nope\r\n\r\ndata: {\"c\":3}\r\n\r\n"])).toEqual([{ c: 3 }]);
  });

  it("stops at [DONE]", async () => {
    expect(await collect(['data: {"a":1}\n\ndata: [DONE]\n\ndata: {"late":1}\n\n'])).toEqual([{ a: 1 }]);
  });
});

describe("toDelta", () => {
  it("extracts content, reasoning, finish, usage, timings and errors", () => {
    expect(toDelta({ choices: [{ delta: { content: "hi" } }] })).toEqual({ content: "hi" });
    expect(toDelta({ choices: [{ delta: { reasoning_content: "hmm" }, finish_reason: "stop" }] })).toEqual({ reasoning: "hmm", finish: "stop" });
    expect(toDelta({ choices: [], usage: { prompt_tokens: 1, completion_tokens: 2 }, timings: { predicted_per_second: 80 } }))
      .toEqual({ usage: { prompt_tokens: 1, completion_tokens: 2 }, timings: { predicted_per_second: 80 } });
    expect(toDelta({ error: { message: "boom" } })).toEqual({ error: "boom" });
  });
});
