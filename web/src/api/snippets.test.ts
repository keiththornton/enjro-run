import { describe, expect, it } from "vitest";
import { type Feature, type Lang, appGuides, snippet } from "./snippets";

const open = { baseUrl: "http://127.0.0.1:8790/v1", model: "qwen2.5-1.5b-instruct:q4_k_m", embeddingModel: "nomic:q4", keyRequired: false };
const keyed = { ...open, baseUrl: "http://192.168.1.55:8790/v1", keyRequired: true };
const LANGS: Lang[] = ["curl", "python", "javascript"];
const FEATURES: Feature[] = ["chat", "stream", "tools", "json", "embeddings"];

describe("snippet", () => {
  it("uses the real base URL and model everywhere", () => {
    for (const l of LANGS) for (const f of FEATURES) {
      const s = snippet(l, f, open);
      expect(s).toContain("http://127.0.0.1:8790/v1");
      expect(s).toContain(f === "embeddings" ? "nomic:q4" : "qwen2.5-1.5b-instruct:q4_k_m");
    }
  });

  it("curl bodies never contain a single quote (it would end the shell quoting)", () => {
    for (const f of FEATURES) {
      const body = snippet("curl", f, open).split("-d '")[1].slice(0, -1);
      expect(body).not.toContain("'");
    }
  });

  it("curl bodies are valid JSON", () => {
    for (const f of FEATURES) {
      const body = snippet("curl", f, open).split("-d '")[1].replace(/'$/, "");
      expect(() => JSON.parse(body)).not.toThrow();
    }
  });

  it("never contains a real key: reads it from STRIXRUN_API_KEY when one is required", () => {
    expect(snippet("curl", "chat", keyed)).toContain('Authorization: Bearer $STRIXRUN_API_KEY');
    expect(snippet("python", "chat", keyed)).toContain('os.environ["STRIXRUN_API_KEY"]');
    expect(snippet("python", "chat", keyed)).toMatch(/^import os/);
    expect(snippet("javascript", "chat", keyed)).toContain("process.env.STRIXRUN_API_KEY");
    expect(snippet("curl", "chat", open)).not.toContain("Authorization");
    expect(snippet("python", "chat", open)).toContain('api_key="not-needed"');
  });

  it("python literals use Python booleans", () => {
    expect(snippet("python", "tools", open)).not.toMatch(/\btrue\b|\bfalse\b|\bnull\b/);
  });

  it("streaming curl disables buffering", () => {
    expect(snippet("curl", "stream", open)).toContain(" -N");
  });
});

describe("appGuides", () => {
  it("fills in URL, model and key guidance", () => {
    const guides = appGuides(keyed);
    expect(guides.map((g) => g.name)).toContain("Open WebUI");
    const text = JSON.stringify(guides);
    expect(text).toContain("http://192.168.1.55:8790/v1");
    expect(text).toContain("your StrixRun API key");
    expect(JSON.stringify(appGuides(open))).toContain("no key needed");
    const lc = guides.find((g) => g.name === "LangChain")!.code!;
    expect(lc.startsWith("import os")).toBe(true);
  });
});
