// Copy-ready code for connecting apps, built from this server's real URL and models.

export type Lang = "curl" | "python" | "javascript";
export type Feature = "chat" | "stream" | "tools" | "json" | "embeddings";

export type SnippetContext = {
  baseUrl: string; // .../v1
  model: string;
  embeddingModel: string;
  keyRequired: boolean;
};

const KEY_ENV = "STRIXRUN_API_KEY";

function curlAuth(c: SnippetContext) {
  return c.keyRequired ? ` \\\n  -H "Authorization: Bearer $${KEY_ENV}"` : "";
}

function pyClient(c: SnippetContext) {
  const key = c.keyRequired ? `os.environ["${KEY_ENV}"]` : `"not-needed"`;
  return `${c.keyRequired ? "import os\n" : ""}from openai import OpenAI

client = OpenAI(base_url="${c.baseUrl}", api_key=${key})`;
}

function jsClient(c: SnippetContext) {
  const key = c.keyRequired ? `process.env.${KEY_ENV}` : `"not-needed"`;
  return `import OpenAI from "openai";

const client = new OpenAI({ baseURL: "${c.baseUrl}", apiKey: ${key} });`;
}

const WEATHER_TOOL = {
  type: "function",
  function: {
    name: "get_weather",
    description: "Get the current weather for a city",
    parameters: { type: "object", properties: { city: { type: "string" } }, required: ["city"] },
  },
};
const PERSON_SCHEMA = {
  type: "object",
  properties: { name: { type: "string" }, age: { type: "integer" } },
  required: ["name", "age"],
};

function indentJson(value: unknown, indent: number) {
  return JSON.stringify(value, null, 2).replace(/\n/g, "\n" + " ".repeat(indent));
}

function pyLiteral(value: unknown, indent: number): string {
  return indentJson(value, indent).replace(/\btrue\b/g, "True").replace(/\bfalse\b/g, "False").replace(/\bnull\b/g, "None");
}

export function snippet(lang: Lang, feature: Feature, c: SnippetContext): string {
  const m = JSON.stringify(c.model);
  const e = JSON.stringify(c.embeddingModel);
  if (lang === "curl") {
    const post = (path: string, body: unknown, extra = "") =>
      `curl -sS ${c.baseUrl}${path}${extra}${curlAuth(c)} \\\n  -H "Content-Type: application/json" \\\n  -d '${indentJson(body, 2)}'`;
    const chat = { model: c.model, messages: [{ role: "user", content: "Hello!" }] };
    switch (feature) {
      case "chat":
        return post("/chat/completions", chat);
      case "stream":
        return post("/chat/completions", { ...chat, stream: true }, " -N");
      case "tools":
        return post("/chat/completions", {
          model: c.model,
          messages: [{ role: "user", content: "What is the weather in Paris?" }],
          tools: [WEATHER_TOOL],
        });
      case "json":
        return post("/chat/completions", {
          model: c.model,
          messages: [{ role: "user", content: "Invent a person." }],
          response_format: { type: "json_schema", json_schema: { name: "person", schema: PERSON_SCHEMA } },
        });
      case "embeddings":
        return post("/embeddings", { model: c.embeddingModel, input: ["first text", "second text"] });
    }
  }
  if (lang === "python") {
    const head = pyClient(c);
    switch (feature) {
      case "chat":
        return `${head}

reply = client.chat.completions.create(
    model=${m},
    messages=[{"role": "user", "content": "Hello!"}],
)
print(reply.choices[0].message.content)`;
      case "stream":
        return `${head}

stream = client.chat.completions.create(
    model=${m},
    messages=[{"role": "user", "content": "Tell me a short story."}],
    stream=True,
)
for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)`;
      case "tools":
        return `${head}

tools = [${pyLiteral(WEATHER_TOOL, 4)}]
reply = client.chat.completions.create(
    model=${m},
    messages=[{"role": "user", "content": "What is the weather in Paris?"}],
    tools=tools,
)
message = reply.choices[0].message
if message.tool_calls:  # the model may also answer directly instead of calling a tool
    for call in message.tool_calls:
        print(call.function.name, call.function.arguments)
else:
    print(message.content)`;
      case "json":
        return `${head}

reply = client.chat.completions.create(
    model=${m},
    messages=[{"role": "user", "content": "Invent a person."}],
    response_format={
        "type": "json_schema",
        "json_schema": {"name": "person", "schema": ${pyLiteral(PERSON_SCHEMA, 8)}},
    },
)
print(reply.choices[0].message.content)  # always valid JSON matching the schema`;
      case "embeddings":
        return `${head}

result = client.embeddings.create(model=${e}, input=["first text", "second text"])
print(len(result.data[0].embedding))`;
    }
  }
  const head = jsClient(c);
  switch (feature) {
    case "chat":
      return `${head}

const reply = await client.chat.completions.create({
  model: ${m},
  messages: [{ role: "user", content: "Hello!" }],
});
console.log(reply.choices[0].message.content);`;
    case "stream":
      return `${head}

const stream = await client.chat.completions.create({
  model: ${m},
  messages: [{ role: "user", content: "Tell me a short story." }],
  stream: true,
});
for await (const chunk of stream) {
  process.stdout.write(chunk.choices[0]?.delta?.content ?? "");
}`;
    case "tools":
      return `${head}

const reply = await client.chat.completions.create({
  model: ${m},
  messages: [{ role: "user", content: "What is the weather in Paris?" }],
  tools: [${indentJson(WEATHER_TOOL, 2)}],
});
const message = reply.choices[0].message;
if (message.tool_calls?.length) {
  // the model may also answer directly instead of calling a tool
  for (const call of message.tool_calls) console.log(call.function.name, call.function.arguments);
} else {
  console.log(message.content);
}`;
    case "json":
      return `${head}

const reply = await client.chat.completions.create({
  model: ${m},
  messages: [{ role: "user", content: "Invent a person." }],
  response_format: {
    type: "json_schema",
    json_schema: { name: "person", schema: ${indentJson(PERSON_SCHEMA, 4)} },
  },
});
console.log(JSON.parse(reply.choices[0].message.content ?? "{}"));`;
    case "embeddings":
      return `${head}

const result = await client.embeddings.create({ model: ${e}, input: ["first text", "second text"] });
console.log(result.data[0].embedding.length);`;
  }
}

export type AppGuide = { name: string; steps: string[]; code?: string; codeLabel?: string };

export function appGuides(c: SnippetContext): AppGuide[] {
  const key = c.keyRequired ? "your StrixRun API key" : "any text (no key needed)";
  return [
    {
      name: "Open WebUI",
      steps: [
        "Open Admin settings → Connections.",
        `Add an OpenAI API connection with the URL ${c.baseUrl}.`,
        `For the key, enter ${key}.`,
        "Save. StrixRun's loaded models appear in the model picker.",
      ],
    },
    {
      name: "Continue (VS Code / JetBrains)",
      steps: ["Add this to your Continue config.yaml:"],
      codeLabel: "config.yaml",
      code: `models:
  - name: ${c.model} (StrixRun)
    provider: openai
    model: ${c.model}
    apiBase: ${c.baseUrl}
    apiKey: ${c.keyRequired ? "<your StrixRun API key>" : "not-needed"}`,
    },
    {
      name: "LangChain",
      steps: ["Use ChatOpenAI with StrixRun as the base URL:"],
      codeLabel: "Python",
      code: `${c.keyRequired ? "import os\n" : ""}from langchain_openai import ChatOpenAI

llm = ChatOpenAI(base_url="${c.baseUrl}", api_key=${c.keyRequired ? `os.environ["${KEY_ENV}"]` : `"not-needed"`},
                 model="${c.model}")
print(llm.invoke("Hello!").content)`,
    },
    {
      name: "llm (command line)",
      steps: ["Add this to extra-openai-models.yaml in llm's config folder (`llm logs path` shows where), then run llm -m strixrun \"Hello\":"],
      codeLabel: "extra-openai-models.yaml",
      code: `- model_id: strixrun
  model_name: ${c.model}
  api_base: "${c.baseUrl}"${c.keyRequired ? `\n  api_key_name: strixrun   # set it with: llm keys set strixrun` : ""}`,
    },
    {
      name: "Anything else OpenAI-compatible",
      steps: [
        `Base URL: ${c.baseUrl}`,
        `API key: ${key}`,
        `Model: ${c.model} (any name from the list above works)`,
      ],
    },
  ];
}
