const DEFAULT_TIMEOUT = 20;

const DEFAULT_PROBES = ["models", "chat_completions", "tool_calling", "responses", "embeddings"];
const DEFAULT_TEXT_MODELS = ["gpt-4.1", "gpt-4o", "claude-sonnet-4-5", "deepseek-chat"];
const DEFAULT_VISION_MODELS = ["gpt-4o", "gpt-4.1", "claude-sonnet-4-5"];
const DEFAULT_EMBEDDING_MODELS = ["text-embedding-3-large", "text-embedding-3-small", "text-embedding-ada-002"];

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

function parseTextareaList(value) {
  const text = String(value || "").trim();
  if (!text) {
    return [];
  }
  try {
    const parsed = JSON.parse(text);
    if (Array.isArray(parsed)) {
      return parsed.map((item) => String(item).trim()).filter(Boolean);
    }
  } catch {}
  return text.replaceAll("\r", "\n").replaceAll(",", "\n").split("\n").map((item) => item.trim()).filter(Boolean);
}

function dedupeStrings(items) {
  const seen = new Set();
  const output = [];
  for (const item of items) {
    const normalized = String(item || "").trim();
    if (!normalized) {
      continue;
    }
    const lowered = normalized.toLowerCase();
    if (seen.has(lowered)) {
      continue;
    }
    seen.add(lowered);
    output.push(normalized);
  }
  return output;
}

function extractVersionScore(modelId) {
  const matches = String(modelId || "").toLowerCase().match(/(\d+(?:\.\d+)*)/);
  if (!matches) {
    return 0;
  }
  try {
    return matches[1].split(".").map(Number).reduce((total, value, index) => total + value / (10 ** index), 0);
  } catch {
    return 0;
  }
}

function textModelScore(modelId) {
  const lower = String(modelId || "").toLowerCase();
  let score = extractVersionScore(lower);
  if (lower.includes("gpt-5")) score += 60;
  else if (lower.includes("gpt-4.1")) score += 56;
  else if (lower.includes("gpt-4o")) score += 54;
  else if (lower.includes("claude")) score += 50;
  else if (lower.includes("gemini")) score += 46;
  else if (lower.includes("deepseek")) score += 42;
  if (["opus", "ultra", "max", "pro"].some((word) => lower.includes(word))) score += 8;
  if (["sonnet", "reasoner", "r1"].some((word) => lower.includes(word))) score += 6;
  if (["mini", "nano", "lite", "flash", "haiku"].some((word) => lower.includes(word))) score -= 6;
  if (["image", "imagine", "embedding", "tts", "whisper", "audio"].some((word) => lower.includes(word))) score -= 30;
  return score;
}

function visionModelScore(modelId) {
  const lower = String(modelId || "").toLowerCase();
  let score = extractVersionScore(lower);
  if (lower.includes("gpt-4o")) score += 60;
  else if (lower.includes("gpt-4.1")) score += 57;
  else if (lower.includes("claude")) score += 53;
  else if (lower.includes("gemini")) score += 50;
  if (["vision", "vl", "image", "omni", "multimodal"].some((word) => lower.includes(word))) score += 12;
  if (["pro", "max"].some((word) => lower.includes(word))) score += 6;
  if (["flash", "lite", "mini", "haiku"].some((word) => lower.includes(word))) score -= 4;
  return score;
}

function embeddingModelScore(modelId) {
  const lower = String(modelId || "").toLowerCase();
  let score = extractVersionScore(lower);
  if (lower.includes("embedding")) score += 50;
  if (lower.includes("large")) score += 6;
  if (lower.includes("small")) score += 2;
  return score;
}

function sortModelsByScore(models, scorer) {
  return dedupeStrings(models).sort((left, right) => {
    const scoreDiff = scorer(right) - scorer(left);
    if (scoreDiff !== 0) {
      return scoreDiff;
    }
    return String(right).localeCompare(String(left));
  });
}

function summarizeResults(results) {
  const passCount = results.filter((item) => item.ok).length;
  const failCount = results.length - passCount;
  const totalElapsed = results.reduce((total, item) => total + Number(item.elapsed_ms || 0), 0);
  const avgElapsed = results.length ? Math.round(totalElapsed / results.length) : 0;
  const slowest = [...results].sort((a, b) => Number(b.elapsed_ms || 0) - Number(a.elapsed_ms || 0))[0] || null;
  const rankings = results.find((item) => item.name === "models")?.details?.rankings || {};
  return {
    pass_count: passCount,
    fail_count: failCount,
    total_elapsed_ms: totalElapsed,
    avg_elapsed_ms: avgElapsed,
    slowest_probe: slowest ? `${slowest.name} (${slowest.elapsed_ms}ms)` : "-",
    rankings,
  };
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
  constructor(baseUrl, apiKey, timeoutSec, options = {}) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.timeoutMs = timeoutSec * 1000;
    this.headers = {
      authorization: `Bearer ${apiKey}`,
      "content-type": "application/json",
      "user-agent": "gateway-prober-pages/0.1",
    };
    this.models = [];
    this.configuredTextModels = options.textModels || [];
    this.configuredVisionModels = options.visionModels || [];
    this.enabledProbes = Array.isArray(options.enabledProbes) && options.enabledProbes.length ? options.enabledProbes : DEFAULT_PROBES.slice();
    this.endpointHints = options.endpointHints || [];
  }

  async request(method, path, body) {
    let normalizedPath = String(path || "").trim();
    if (!normalizedPath.startsWith("/")) {
      normalizedPath = `/${normalizedPath}`;
    }
    let url = `${this.baseUrl}${normalizedPath}`;
    if (this.baseUrl.endsWith("/v1") && normalizedPath.startsWith("/v1/")) {
      url = `${this.baseUrl}${normalizedPath.slice(3)}`;
    }
    return fetchWithTimeout(url, {
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

  looksLikeVisionModel(modelId) {
    const lower = String(modelId || "").toLowerCase();
    return ["vision", "vl", "image", "gpt-4o", "gpt-4.1", "claude", "gemini"].some((keyword) => lower.includes(keyword));
  }

  pickTextProbeModels() {
    const configured = dedupeStrings(this.configuredTextModels);
    if (configured.length) {
      return sortModelsByScore(configured, textModelScore).slice(0, 6);
    }
    const modelsFromApi = this.models.map((item) => item.id || "").filter(Boolean);
    const filtered = modelsFromApi.filter((modelId) => !["image", "imagine", "embedding", "tts", "whisper"].some((word) => modelId.toLowerCase().includes(word)));
    return sortModelsByScore(filtered.concat(DEFAULT_TEXT_MODELS), textModelScore).slice(0, 6);
  }

  pickVisionProbeModels() {
    const configured = dedupeStrings(this.configuredVisionModels);
    if (configured.length) {
      return sortModelsByScore(configured, visionModelScore).slice(0, 6);
    }
    const modelsFromApi = this.models.map((item) => item.id || "").filter(Boolean);
    const filtered = modelsFromApi.filter((modelId) => this.looksLikeVisionModel(modelId));
    if (filtered.length) {
      return sortModelsByScore(filtered.concat(DEFAULT_VISION_MODELS), visionModelScore).slice(0, 6);
    }
    return sortModelsByScore(DEFAULT_VISION_MODELS.slice(), visionModelScore).slice(0, 6);
  }

  pickEmbeddingProbeModels() {
    const configured = dedupeStrings(this.configuredTextModels);
    const modelsFromApi = this.models.map((item) => item.id || "").filter(Boolean);
    const embeddingModels = modelsFromApi.filter((modelId) => modelId.toLowerCase().includes("embedding"));
    return sortModelsByScore(configured.concat(embeddingModels, DEFAULT_EMBEDDING_MODELS), embeddingModelScore).slice(0, 6);
  }

  pickImageGenerationModels() {
    const configured = dedupeStrings(this.configuredVisionModels);
    const modelsFromApi = this.models.map((item) => item.id || "").filter(Boolean);
    const imageModels = modelsFromApi.filter((modelId) => ["image", "imagine", "gpt-image", "dall-e", "flux", "sd"].some((keyword) => modelId.toLowerCase().includes(keyword)));
    return sortModelsByScore(configured.concat(imageModels), visionModelScore).slice(0, 6);
  }

  bestChatModel() {
    return this.pickTextProbeModels()[0] || null;
  }

  bestImageModel() {
    return this.pickImageGenerationModels()[0] || null;
  }

  shouldStopRetry(statusCode, body = "") {
    if ([401, 403, 404, 405, 415, 429, 501].includes(statusCode)) {
      return true;
    }
    const lowered = String(body || "").toLowerCase();
    return ["unknown url", "not found", "method not allowed", "unsupported media type", "invalid api key", "authentication", "authorization"].some((signal) => lowered.includes(signal));
  }

  async tryModelCandidates(models, runner) {
    const attempts = [];
    for (const model of dedupeStrings(models)) {
      let result;
      try {
        result = await runner(model);
      } catch (error) {
        result = {
          ok: false,
          status_code: null,
          details: {
            model,
            error: error.name === "AbortError" ? "request timed out" : String(error.message || error),
          },
          stop_retry: false,
        };
      }
      const sanitized = {
        ok: Boolean(result.ok),
        status_code: result.status_code ?? null,
        details: result.details || {},
        stop_retry: Boolean(result.stop_retry),
      };
      attempts.push(sanitized);
      if (sanitized.ok) {
        return { ok: true, status_code: sanitized.status_code, details: sanitized.details, attempts };
      }
      if (sanitized.stop_retry) {
        break;
      }
    }
    return { ok: false, status_code: attempts.at(-1)?.status_code ?? null, attempts };
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
    details.rankings = {
      text: this.pickTextProbeModels(),
      vision: this.pickVisionProbeModels(),
      embeddings: this.pickEmbeddingProbeModels(),
      images: this.pickImageGenerationModels(),
    };
    return { ok: true, status_code: response.status, summary: `listed ${this.models.length} model(s)`, details };
  }

  async probeChat() {
    const candidateModels = this.pickTextProbeModels();
    if (!candidateModels.length) {
      return { ok: false, status_code: null, summary: "no text model found", details: {} };
    }
    const result = await this.tryModelCandidates(candidateModels, async (model) => {
      const response = await this.request("POST", "/v1/chat/completions", {
        model,
        messages: [{ role: "user", content: "Reply with exactly: OK_CHAT" }],
        temperature: 0,
        max_tokens: 20,
      });
      const details = { model, status_code: response.status, endpoint: "/v1/chat/completions", url: `${this.baseUrl}/v1/chat/completions` };
      if (!response.ok) {
        details.body = summarizeErrorBody(await response.text());
        return { ok: false, status_code: response.status, details, stop_retry: this.shouldStopRetry(response.status, details.body) };
      }
      const payload = await response.json();
      const content = payload.choices?.[0]?.message?.content ?? null;
      details.content = content;
      details.finish_reason = payload.choices?.[0]?.finish_reason ?? null;
      return { ok: content === "OK_CHAT", status_code: response.status, details };
    });
    if (!result.ok) {
      return { ok: false, status_code: result.status_code, summary: "chat/completions failed", details: { candidate_models: candidateModels, attempts: result.attempts } };
    }
    return {
      ok: true,
      status_code: result.status_code,
      summary: "chat/completions works",
      details: { ...result.details, attempts: result.attempts },
    };
  }

  async probeTools() {
    const candidateModels = this.pickTextProbeModels();
    if (!candidateModels.length) {
      return { ok: false, status_code: null, summary: "no text model found", details: {} };
    }
    const result = await this.tryModelCandidates(candidateModels, async (model) => {
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
      const details = { model, status_code: response.status, endpoint: "/v1/chat/completions", url: `${this.baseUrl}/v1/chat/completions` };
      if (!response.ok) {
        details.body = summarizeErrorBody(await response.text());
        return { ok: false, status_code: response.status, details, stop_retry: this.shouldStopRetry(response.status, details.body) };
      }
      const payload = await response.json();
      const toolCalls = payload.choices?.[0]?.message?.tool_calls || [];
      details.tool_calls = toolCalls;
      return { ok: Boolean(toolCalls[0]?.function?.name === "get_status"), status_code: response.status, details };
    });
    if (!result.ok) {
      return { ok: false, status_code: result.status_code, summary: "tool calling failed", details: { candidate_models: candidateModels, attempts: result.attempts } };
    }
    return {
      ok: true,
      status_code: result.status_code,
      summary: "tool calling works",
      details: { ...result.details, attempts: result.attempts },
    };
  }

  async probeResponses() {
    const candidateModels = this.pickTextProbeModels();
    if (!candidateModels.length) {
      return { ok: false, status_code: null, summary: "no text model found", details: {} };
    }
    const result = await this.tryModelCandidates(candidateModels, async (model) => {
      const response = await this.request("POST", "/v1/responses", {
        model,
        input: "Reply with exactly: OK_RESPONSES",
        max_output_tokens: 20,
      });
      const details = { model, status_code: response.status, endpoint: "/v1/responses", url: `${this.baseUrl}/v1/responses` };
      if (!response.ok) {
        details.body = summarizeErrorBody(await response.text());
        return { ok: false, status_code: response.status, details, stop_retry: this.shouldStopRetry(response.status, details.body) };
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
      return { ok: outputText.includes("OK_RESPONSES"), status_code: response.status, details };
    });
    if (!result.ok) {
      return { ok: false, status_code: result.status_code, summary: "responses API failed", details: { candidate_models: candidateModels, attempts: result.attempts } };
    }
    return {
      ok: true,
      status_code: result.status_code,
      summary: "responses API works",
      details: { ...result.details, attempts: result.attempts },
    };
  }

  async probeEmbeddings() {
    const candidateModels = this.pickEmbeddingProbeModels();
    const result = await this.tryModelCandidates(candidateModels, async (model) => {
      const response = await this.request("POST", "/v1/embeddings", {
        model,
        input: "macro rotation",
      });
      const details = { model, status_code: response.status, endpoint: "/v1/embeddings", url: `${this.baseUrl}/v1/embeddings` };
      if (!response.ok) {
        details.body = summarizeErrorBody(await response.text());
        return { ok: false, status_code: response.status, details, stop_retry: this.shouldStopRetry(response.status, details.body) };
      }
      const payload = await response.json();
      const vector = payload.data?.[0]?.embedding;
      details.vector_length = Array.isArray(vector) ? vector.length : null;
      return { ok: Array.isArray(vector) && vector.length > 0, status_code: response.status, details };
    });
    if (!result.ok) {
      return { ok: false, status_code: result.status_code, summary: "embeddings failed", details: { candidate_models: candidateModels, attempts: result.attempts } };
    }
    return {
      ok: true,
      status_code: result.status_code,
      summary: "embeddings work",
      details: { ...result.details, attempts: result.attempts },
    };
  }

  async probeImages() {
    const candidateModels = this.pickImageGenerationModels();
    if (!candidateModels.length) {
      return { ok: false, status_code: null, summary: "no image model found", details: {} };
    }
    const result = await this.tryModelCandidates(candidateModels, async (model) => {
      const response = await this.request("POST", "/v1/images/generations", {
        model,
        prompt: "A red square on white background",
        size: "256x256",
      });
      const details = { model, status_code: response.status, endpoint: "/v1/images/generations", url: `${this.baseUrl}/v1/images/generations` };
      if (!response.ok) {
        details.body = summarizeErrorBody(await response.text());
        return { ok: false, status_code: response.status, details, stop_retry: this.shouldStopRetry(response.status, details.body) };
      }
      const payload = await response.json();
      const first = payload.data?.[0] || {};
      details.image_fields = Object.keys(first).sort();
      return { ok: Boolean(first.b64_json || first.url), status_code: response.status, details };
    });
    if (!result.ok) {
      return { ok: false, status_code: result.status_code, summary: "image generation failed", details: { candidate_models: candidateModels, attempts: result.attempts } };
    }
    return {
      ok: true,
      status_code: result.status_code,
      summary: "image generation works",
      details: { ...result.details, attempts: result.attempts },
    };
  }

  async probeCapabilities() {
    const endpointSupport = {
      "/v1/chat/completions": { kind: "chat", url: `${this.baseUrl}/v1/chat/completions`, supported: false, text_supported: false, vision_supported: false },
      "/v1/responses": { kind: "responses", url: `${this.baseUrl}/v1/responses`, supported: false, text_supported: false, vision_supported: false },
      "/v1/embeddings": { kind: "embeddings", url: `${this.baseUrl}/v1/embeddings`, supported: false, text_supported: false, vision_supported: false },
      "/v1/images/generations": { kind: "images", url: `${this.baseUrl}/v1/images/generations`, supported: false, text_supported: false, vision_supported: false },
    };
    const models = [];
    for (const model of this.pickTextProbeModels()) {
      const perEndpoint = {};
      let textSupported = false;
      let lastStatus = null;
      let lastBody = "";
      const checks = [
        ["/v1/chat/completions", { model, messages: [{ role: "user", content: "Reply with exactly: OK_TEXT" }], max_tokens: 12 }],
        ["/v1/responses", { model, input: "Reply with exactly: OK_TEXT", max_output_tokens: 12 }],
        ["/v1/embeddings", { model, input: "macro rotation" }],
        ["/v1/images/generations", { model, prompt: "A red square on white background", size: "256x256" }],
      ];
      for (const [path, body] of checks) {
        const response = await this.request("POST", path, body);
        perEndpoint[path] = {
          url: `${this.baseUrl}${path}`,
          status_code: response.status,
          text_supported: response.ok,
          vision_supported: false,
        };
        if (response.ok) {
          endpointSupport[path].supported = true;
          endpointSupport[path].text_supported = true;
          textSupported = true;
        }
        lastStatus = response.status;
        lastBody = summarizeErrorBody(await response.text());
      }
      models.push({
        name: model,
        kind: "text",
        ok: textSupported,
        status_code: lastStatus,
        summary: textSupported ? "text probing passed" : "text probing failed",
        details: {
          capabilities: { text: textSupported, vision: null },
          endpoint_support: perEndpoint,
          last_error_body: textSupported ? "" : lastBody,
        },
      });
    }
    return {
      ok: models.some((item) => item.ok),
      status_code: null,
      summary: "capabilities scan completed",
      details: {
        base_url: this.baseUrl,
        endpoint_support: endpointSupport,
        models,
      },
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

  async probeExtraEndpoints() {
    const endpoints = [];
    for (const hint of dedupeStrings(this.endpointHints)) {
      let path = hint;
      if (!path.startsWith("http://") && !path.startsWith("https://")) {
        path = path.startsWith("/") ? path : `/${path}`;
      }
      const url = path.startsWith("http://") || path.startsWith("https://") ? path : `${this.baseUrl}${path}`;
      const entry = { path: hint, url };
      try {
        let response = await this.request("OPTIONS", path, null);
        entry.options_status = response.status;
        if (response.status >= 400) {
          response = await this.request("GET", path, null);
          entry.get_status = response.status;
          entry.content_type = response.headers.get("content-type") || "";
        } else {
          entry.allow = response.headers.get("allow") || "";
        }
      } catch (error) {
        entry.error = String(error.message || error);
      }
      endpoints.push(entry);
    }
    if (!endpoints.length) {
      return { ok: true, status_code: null, summary: "no extra endpoints configured", details: { endpoints: [] } };
    }
    const ok = endpoints.some((item) => [item.options_status, item.get_status].some((status) => Number.isFinite(status) && status < 500));
    return { ok, status_code: null, summary: "extra endpoint probing finished", details: { endpoints } };
  }

  async run() {
    const probeMap = {
      models: () => this.probeModels(),
      chat_completions: () => this.probeChat(),
      tool_calling: () => this.probeTools(),
      responses: () => this.probeResponses(),
      embeddings: () => this.probeEmbeddings(),
      images: () => this.probeImages(),
      docs: () => this.probeDocs(),
      extra_endpoints: () => this.probeExtraEndpoints(),
      capabilities: () => this.probeCapabilities(),
    };
    const results = [];
    for (const name of this.enabledProbes) {
      if (!probeMap[name]) continue;
      results.push(await this.runProbe(name, probeMap[name]));
    }
    return results;
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
  const enabledProbes = Array.isArray(payload.enabled_probes) && payload.enabled_probes.length
    ? payload.enabled_probes.map(String)
    : DEFAULT_PROBES.slice();
  const textModels = parseTextareaList(payload.text_models || "");
  const visionModels = parseTextareaList(payload.vision_models || "");
  const endpointHints = parseTextareaList(payload.endpoint_paths || "");

  if (!baseUrl || !apiKey) {
    return json({ error: "Base URL 和 API Key 都是必填。" }, 400);
  }
  if (!/^https:\/\/[^/\s]+/i.test(baseUrl)) {
    return json({ error: "Cloudflare Pages 线上版只支持公网 https 地址。需要本地 http 调试时，请使用 Flask 版。" }, 400);
  }
  if (!Number.isFinite(timeout) || timeout < 1 || timeout > 120) {
    return json({ error: "Timeout must be between 1 and 120 seconds" }, 400);
  }

  const prober = new GatewayProber(baseUrl, apiKey, timeout, {
    enabledProbes,
    textModels,
    visionModels,
    endpointHints,
  });
  const results = await prober.run();
  return json({ results, summary: summarizeResults(results) });
}
