const DEFAULT_TIMEOUT = 20;

function json(data, status = 200) {
  return new Response(JSON.stringify(data, null, 2), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

function summarizeErrorBody(text) {
  return (text || "").slice(0, 500);
}

async function readJson(request) {
  try {
    return await request.json();
  } catch {
    return null;
  }
}

async function fetchWithTimeout(url, options, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

class GatewayProber {
  constructor(baseUrl, apiKey, timeoutSec) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.timeoutMs = timeoutSec * 1000;
    this.headers = {
      authorization: `Bearer ${apiKey}`,
      "content-type": "application/json",
      "user-agent": "gateway-prober-pages/0.1",
    };
    this.models = [];
  }

  async request(method, path, body) {
    return fetchWithTimeout(`${this.baseUrl}${path}`, {
      method,
      headers: this.headers,
      body: body ? JSON.stringify(body) : undefined,
    }, this.timeoutMs);
  }

  async runProbe(name, fn) {
    const started = Date.now();
    try {
      const payload = await fn();
      return {
        name,
        ok: payload.ok,
        status_code: payload.status_code ?? null,
        summary: payload.summary,
        details: payload.details ?? {},
        elapsed_ms: Date.now() - started,
      };
    } catch (error) {
      return {
        name,
        ok: false,
        status_code: null,
        summary: error.name === "AbortError" ? "request timed out" : String(error.message || error),
        details: {},
        elapsed_ms: Date.now() - started,
      };
    }
  }

  bestChatModel() {
    const preferred = ["gpt-5.4", "gpt-5.3-codex", "gpt-5.2-codex", "gpt-5.1-codex", "gpt-5-codex", "gpt-5"];
    const ids = this.models.map((item) => item.id || "");
    for (const id of preferred) {
      if (ids.includes(id)) {
        return id;
      }
    }
    return ids.find((id) => {
      const lower = id.toLowerCase();
      return !lower.includes("image") && !lower.includes("imagine") && !lower.includes("embedding");
    }) || null;
  }

  bestImageModel() {
    return this.models.find((item) => {
      const lower = (item.id || "").toLowerCase();
      return lower.includes("image") || lower.includes("imagine");
    })?.id || null;
  }

  async probeModels() {
    const response = await this.request("GET", "/v1/models");
    const details = { status_code: response.status };
    if (!response.ok) {
      details.body = summarizeErrorBody(await response.text());
      return { ok: false, status_code: response.status, summary: "failed to list models", details };
    }
    const payload = await response.json();
    this.models = payload.data || [];
    details.model_count = this.models.length;
    details.model_ids = this.models.map((item) => item.id);
    return { ok: true, status_code: response.status, summary: `listed ${this.models.length} model(s)`, details };
  }

  async probeChat() {
    const model = this.bestChatModel();
    if (!model) {
      return { ok: false, status_code: null, summary: "no text model found", details: {} };
    }
    const response = await this.request("POST", "/v1/chat/completions", {
      model,
      messages: [{ role: "user", content: "Reply with exactly: OK_CHAT" }],
      temperature: 0,
      max_tokens: 20,
    });
    const details = { model, status_code: response.status };
    if (!response.ok) {
      details.body = summarizeErrorBody(await response.text());
      return { ok: false, status_code: response.status, summary: "chat/completions failed", details };
    }
    const payload = await response.json();
    const content = payload.choices?.[0]?.message?.content ?? null;
    details.content = content;
    details.finish_reason = payload.choices?.[0]?.finish_reason ?? null;
    return {
      ok: content === "OK_CHAT",
      status_code: response.status,
      summary: content === "OK_CHAT" ? "chat/completions works" : "chat/completions returned unexpected output",
      details,
    };
  }

  async probeTools() {
    const model = this.bestChatModel();
    if (!model) {
      return { ok: false, status_code: null, summary: "no text model found", details: {} };
    }
    const response = await this.request("POST", "/v1/chat/completions", {
      model,
      messages: [{ role: "user", content: 'Use the tool named get_status with argument {"asset":"equity"}.' }],
      tools: [
        {
          type: "function",
          function: {
            name: "get_status",
            description: "Return a market status",
            parameters: {
              type: "object",
              properties: { asset: { type: "string" } },
              required: ["asset"],
            },
          },
        },
      ],
      tool_choice: "auto",
      temperature: 0,
      max_tokens: 80,
    });
    const details = { model, status_code: response.status };
    if (!response.ok) {
      details.body = summarizeErrorBody(await response.text());
      return { ok: false, status_code: response.status, summary: "tool calling failed", details };
    }
    const payload = await response.json();
    const toolCalls = payload.choices?.[0]?.message?.tool_calls || [];
    details.tool_calls = toolCalls;
    return {
      ok: Boolean(toolCalls[0]?.function?.name === "get_status"),
      status_code: response.status,
      summary: toolCalls[0]?.function?.name === "get_status" ? "tool calling works" : "tool calling unavailable or malformed",
      details,
    };
  }

  async probeResponses() {
    const model = this.bestChatModel();
    if (!model) {
      return { ok: false, status_code: null, summary: "no text model found", details: {} };
    }
    const response = await this.request("POST", "/v1/responses", {
      model,
      input: "Reply with exactly: OK_RESPONSES",
      max_output_tokens: 20,
    });
    const details = { model, status_code: response.status };
    if (!response.ok) {
      details.body = summarizeErrorBody(await response.text());
      return { ok: false, status_code: response.status, summary: "responses API failed", details };
    }
    const payload = await response.json();
    details.response_keys = Object.keys(payload);
    let outputText = "";
    for (const item of payload.output || []) {
      for (const content of item.content || []) {
        if (content.type === "output_text" || content.type === "text") {
          outputText += content.text || "";
        }
      }
    }
    details.output_text = outputText;
    return {
      ok: outputText.includes("OK_RESPONSES"),
      status_code: response.status,
      summary: outputText.includes("OK_RESPONSES") ? "responses API works" : "responses API returned unexpected payload",
      details,
    };
  }

  async probeEmbeddings() {
    const response = await this.request("POST", "/v1/embeddings", {
      model: this.bestChatModel() || "text-embedding-3-small",
      input: "macro rotation",
    });
    const details = { status_code: response.status };
    if (!response.ok) {
      details.body = summarizeErrorBody(await response.text());
      return { ok: false, status_code: response.status, summary: "embeddings failed", details };
    }
    const payload = await response.json();
    const vector = payload.data?.[0]?.embedding;
    details.vector_length = Array.isArray(vector) ? vector.length : null;
    return {
      ok: Array.isArray(vector) && vector.length > 0,
      status_code: response.status,
      summary: Array.isArray(vector) && vector.length > 0 ? "embeddings work" : "embeddings payload malformed",
      details,
    };
  }

  async probeImages() {
    const model = this.bestImageModel();
    if (!model) {
      return { ok: false, status_code: null, summary: "no image model found", details: {} };
    }
    const response = await this.request("POST", "/v1/images/generations", {
      model,
      prompt: "A red square on white background",
      size: "1024x1024",
    });
    const details = { model, status_code: response.status };
    if (!response.ok) {
      details.body = summarizeErrorBody(await response.text());
      return { ok: false, status_code: response.status, summary: "image generation failed", details };
    }
    const payload = await response.json();
    const first = payload.data?.[0] || {};
    details.image_fields = Object.keys(first).sort();
    return {
      ok: Boolean(first.b64_json || first.url),
      status_code: response.status,
      summary: first.b64_json || first.url ? "image generation works" : "image payload malformed",
      details,
    };
  }

  async probeDocs() {
    const endpoints = [];
    for (const path of ["/docs", "/openapi.json", "/health", "/version"]) {
      try {
        const response = await this.request("GET", path);
        endpoints.push({
          path,
          status_code: response.status,
          content_type: response.headers.get("content-type") || "",
        });
      } catch (error) {
        endpoints.push({
          path,
          status_code: null,
          content_type: error.name === "AbortError" ? "timeout" : "error",
        });
      }
    }
    return {
      ok: true,
      status_code: null,
      summary: "collected documentation endpoint metadata",
      details: { endpoints },
    };
  }

  async run() {
    return [
      await this.runProbe("models", () => this.probeModels()),
      await this.runProbe("chat_completions", () => this.probeChat()),
      await this.runProbe("tool_calling", () => this.probeTools()),
      await this.runProbe("responses", () => this.probeResponses()),
      await this.runProbe("embeddings", () => this.probeEmbeddings()),
      await this.runProbe("images", () => this.probeImages()),
      await this.runProbe("docs", () => this.probeDocs()),
    ];
  }
}

export async function onRequestPost(context) {
  const payload = await readJson(context.request);
  if (!payload) {
    return json({ error: "Invalid JSON body" }, 400);
  }

  const baseUrl = String(payload.base_url || "").trim();
  const apiKey = String(payload.api_key || "").trim();
  const timeout = Number(payload.timeout || DEFAULT_TIMEOUT);

  if (!baseUrl || !apiKey) {
    return json({ error: "Base URL and API Key are required" }, 400);
  }
  if (!/^https:\/\/[^/\s]+/i.test(baseUrl)) {
    return json({ error: "Base URL must start with https://" }, 400);
  }
  if (!Number.isFinite(timeout) || timeout < 1 || timeout > 120) {
    return json({ error: "Timeout must be between 1 and 120 seconds" }, 400);
  }

  const prober = new GatewayProber(baseUrl, apiKey, timeout);
  const results = await prober.run();
  return json({ results });
}
