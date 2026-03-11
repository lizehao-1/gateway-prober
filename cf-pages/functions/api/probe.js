const DEFAULT_TIMEOUT = 20;

const DEFAULT_PROBES = ["models", "chat_completions", "tool_calling", "responses", "embeddings"];
const DEFAULT_ENDPOINT_CANDIDATES = {
  chat: ["/v1/chat/completions"],
  responses: ["/v1/responses", "/v1/responses/compact"],
  embeddings: ["/v1/embeddings"],
  images: ["/v1/images/generations"],
};
const QUICK_API_ROOT_CANDIDATES = ["/v1", ""];
const DEEP_API_ROOT_CANDIDATES = ["/v1", "", "/openai/v1", "/api/v1", "/api/openai/v1"];
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

function matchesAnySuffix(path, suffixes) {
  for (const suffix of suffixes) {
    if (String(path || "").endsWith(suffix)) {
      return suffix;
    }
  }
  return null;
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
    this.probeMode = options.probeMode === "deep" ? "deep" : "quick";
    this.endpointStrategy = options.endpointStrategy === "custom_only" ? "custom_only" : "append";
    this.apiRoots = this.resolveApiRoots();
    this.endpointCandidates = this.resolveEndpointCandidates();
    this.chatEndpoint = this.pickEndpoint("chat");
    this.responsesEndpoint = this.pickEndpoint("responses");
    this.embeddingsEndpoint = this.pickEndpoint("embeddings");
    this.imagesEndpoint = this.pickEndpoint("images");
  }

  async request(method, pathOrUrl, body) {
    const raw = String(pathOrUrl || "").trim();
    if (!raw) {
      throw new Error("Missing request path");
    }
    const url = /^https?:\/\//i.test(raw) ? raw : this.toAbsoluteUrl(raw);
    return fetchWithTimeout(url, {
      method,
      headers: this.headers,
      body: body ? JSON.stringify(body) : undefined,
    }, this.timeoutMs);
  }

  toAbsoluteUrl(path) {
    let normalizedPath = String(path || "").trim();
    if (!normalizedPath.startsWith("/")) {
      normalizedPath = `/${normalizedPath}`;
    }
    let url = `${this.baseUrl}${normalizedPath}`;
    if (this.baseUrl.endsWith("/v1") && normalizedPath.startsWith("/v1/")) {
      url = `${this.baseUrl}${normalizedPath.slice(3)}`;
    }
    return url;
  }

  joinEndpoint(basePath, endpointPath) {
    const normalizedBase = String(basePath || "").replace(/\/+$/, "");
    let normalizedEndpoint = String(endpointPath || "").trim();
    if (!normalizedEndpoint) {
      return normalizedBase || "/";
    }
    if (!normalizedEndpoint.startsWith("/")) {
      normalizedEndpoint = `/${normalizedEndpoint}`;
    }
    if (!normalizedBase) {
      return normalizedEndpoint;
    }
    if (normalizedBase.endsWith("/v1") && normalizedEndpoint.startsWith("/v1/")) {
      return `${normalizedBase}${normalizedEndpoint.slice(3)}`;
    }
    return `${normalizedBase}${normalizedEndpoint}`;
  }

  kindForPath(path) {
    const lowered = String(path || "").toLowerCase();
    if (lowered.endsWith("/chat/completions")) return "chat";
    if (lowered.endsWith("/responses") || lowered.endsWith("/responses/compact")) return "responses";
    if (lowered.endsWith("/embeddings")) return "embeddings";
    if (lowered.endsWith("/images/generations")) return "images";
    return "responses";
  }

  resolveApiRoots() {
    const parsed = new URL(this.baseUrl);
    const path = parsed.pathname.replace(/\/+$/, "");
    const allSuffixes = Object.values(DEFAULT_ENDPOINT_CANDIDATES).flat();
    const matchedSuffix = matchesAnySuffix(path, allSuffixes);
    if (matchedSuffix) {
      const rootPath = path.slice(0, -matchedSuffix.length) || "";
      const seed = `${parsed.origin}${rootPath}`.replace(/\/+$/, "");
      const candidates = [seed];
      if (!seed.endsWith("/v1")) {
        candidates.push(`${seed}/v1`.replace(/\/+$/, ""));
      }
      return dedupeStrings(candidates.filter(Boolean));
    }
    const candidatePaths = this.probeMode === "deep" ? DEEP_API_ROOT_CANDIDATES : QUICK_API_ROOT_CANDIDATES;
    const roots = [];
    if (path) {
      roots.push(`${parsed.origin}${path}`.replace(/\/+$/, ""));
    }
    for (const candidatePath of candidatePaths) {
      roots.push(`${parsed.origin}${candidatePath}`.replace(/\/+$/, ""));
    }
    return dedupeStrings(roots.filter(Boolean));
  }

  resolveEndpointCandidates() {
    const candidates = [];
    const addCandidate = (url, label, kind) => {
      const normalized = String(url || "").replace(/\/+$/, "");
      if (!normalized) return;
      if (candidates.some((item) => item.url === normalized)) return;
      candidates.push({ url: normalized, label, kind });
    };

    if (this.endpointStrategy !== "custom_only" || !this.endpointHints.length) {
      for (const root of this.apiRoots) {
        const parsed = new URL(root);
        const rootPath = parsed.pathname.replace(/\/+$/, "");
        for (const [kind, suffixes] of Object.entries(DEFAULT_ENDPOINT_CANDIDATES)) {
          for (const suffix of suffixes) {
            const fullPath = this.joinEndpoint(rootPath, suffix);
            addCandidate(`${parsed.origin}${fullPath}`, fullPath, kind);
          }
        }
      }
    }

    for (const hint of dedupeStrings(this.endpointHints)) {
      if (/^https?:\/\//i.test(hint)) {
        const parsed = new URL(hint);
        const hintPath = parsed.pathname.replace(/\/+$/, "") || hint;
        addCandidate(hint, hintPath, this.kindForPath(hintPath));
        continue;
      }
      for (const root of this.apiRoots) {
        const parsed = new URL(root);
        const rootPath = parsed.pathname.replace(/\/+$/, "");
        const fullPath = this.joinEndpoint(rootPath, hint);
        addCandidate(`${parsed.origin}${fullPath}`, fullPath, this.kindForPath(fullPath));
      }
    }
    return candidates;
  }

  pickEndpoint(kind) {
    return this.endpointCandidates.find((item) => item.kind === kind) || null;
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
    const attempts = [];
    for (const root of this.apiRoots) {
      const modelsUrl = `${root}/models`;
      const response = await this.request("GET", modelsUrl);
      const attempt = { url: modelsUrl, status_code: response.status };
      if (!response.ok) {
        attempt.body = summarizeErrorBody(await response.text());
        attempts.push(attempt);
        continue;
      }
      const payload = await response.json();
      this.models = payload.data || [];
      attempt.model_count = this.models.length;
      attempts.push(attempt);
      return {
        ok: true,
        status_code: response.status,
        summary: `listed ${this.models.length} model(s)`,
        details: {
          url: modelsUrl,
          api_roots: this.apiRoots,
          attempts,
          model_count: this.models.length,
          model_ids: this.models.map((item) => item.id),
          rankings: {
            text: this.pickTextProbeModels(),
            vision: this.pickVisionProbeModels(),
            embeddings: this.pickEmbeddingProbeModels(),
            images: this.pickImageGenerationModels(),
          },
        },
      };
    }
    return { ok: false, status_code: attempts.at(-1)?.status_code ?? null, summary: "failed to list models", details: { api_roots: this.apiRoots, attempts } };
  }

  async probeChat() {
    const endpoint = this.chatEndpoint;
    if (!endpoint) {
      return { ok: false, status_code: null, summary: "chat endpoint not configured", details: {} };
    }
    const candidateModels = this.pickTextProbeModels();
    if (!candidateModels.length) {
      return { ok: false, status_code: null, summary: "no text model found", details: {} };
    }
    const result = await this.tryModelCandidates(candidateModels, async (model) => {
      const response = await this.request("POST", endpoint.url, {
        model,
        messages: [{ role: "user", content: "Reply with exactly: OK_CHAT" }],
        temperature: 0,
        max_tokens: 20,
      });
      const details = { model, status_code: response.status, endpoint: endpoint.label, url: endpoint.url };
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
      return { ok: false, status_code: result.status_code, summary: "chat/completions failed", details: { endpoint: endpoint.label, url: endpoint.url, candidate_models: candidateModels, attempts: result.attempts } };
    }
    return {
      ok: true,
      status_code: result.status_code,
      summary: "chat/completions works",
      details: { ...result.details, attempts: result.attempts },
    };
  }

  async probeTools() {
    const endpoint = this.chatEndpoint;
    if (!endpoint) {
      return { ok: false, status_code: null, summary: "chat endpoint not configured", details: {} };
    }
    const candidateModels = this.pickTextProbeModels();
    if (!candidateModels.length) {
      return { ok: false, status_code: null, summary: "no text model found", details: {} };
    }
    const result = await this.tryModelCandidates(candidateModels, async (model) => {
      const response = await this.request("POST", endpoint.url, {
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
      const details = { model, status_code: response.status, endpoint: endpoint.label, url: endpoint.url };
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
      return { ok: false, status_code: result.status_code, summary: "tool calling failed", details: { endpoint: endpoint.label, url: endpoint.url, candidate_models: candidateModels, attempts: result.attempts } };
    }
    return {
      ok: true,
      status_code: result.status_code,
      summary: "tool calling works",
      details: { ...result.details, attempts: result.attempts },
    };
  }

  async probeResponses() {
    const endpoint = this.responsesEndpoint;
    if (!endpoint) {
      return { ok: false, status_code: null, summary: "responses endpoint not configured", details: {} };
    }
    const candidateModels = this.pickTextProbeModels();
    if (!candidateModels.length) {
      return { ok: false, status_code: null, summary: "no text model found", details: {} };
    }
    const result = await this.tryModelCandidates(candidateModels, async (model) => {
      const response = await this.request("POST", endpoint.url, {
        model,
        input: "Reply with exactly: OK_RESPONSES",
        max_output_tokens: 20,
      });
      const details = { model, status_code: response.status, endpoint: endpoint.label, url: endpoint.url };
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
      return { ok: false, status_code: result.status_code, summary: "responses API failed", details: { endpoint: endpoint.label, url: endpoint.url, candidate_models: candidateModels, attempts: result.attempts } };
    }
    return {
      ok: true,
      status_code: result.status_code,
      summary: "responses API works",
      details: { ...result.details, attempts: result.attempts },
    };
  }

  async probeEmbeddings() {
    const endpoint = this.embeddingsEndpoint;
    if (!endpoint) {
      return { ok: false, status_code: null, summary: "embeddings endpoint not configured", details: {} };
    }
    const candidateModels = this.pickEmbeddingProbeModels();
    const result = await this.tryModelCandidates(candidateModels, async (model) => {
      const response = await this.request("POST", endpoint.url, {
        model,
        input: "macro rotation",
      });
      const details = { model, status_code: response.status, endpoint: endpoint.label, url: endpoint.url };
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
      return { ok: false, status_code: result.status_code, summary: "embeddings failed", details: { endpoint: endpoint.label, url: endpoint.url, candidate_models: candidateModels, attempts: result.attempts } };
    }
    return {
      ok: true,
      status_code: result.status_code,
      summary: "embeddings work",
      details: { ...result.details, attempts: result.attempts },
    };
  }

  async probeImages() {
    const endpoint = this.imagesEndpoint;
    if (!endpoint) {
      return { ok: false, status_code: null, summary: "image endpoint not configured", details: {} };
    }
    const candidateModels = this.pickImageGenerationModels();
    if (!candidateModels.length) {
      return { ok: false, status_code: null, summary: "no image model found", details: {} };
    }
    const result = await this.tryModelCandidates(candidateModels, async (model) => {
      const response = await this.request("POST", endpoint.url, {
        model,
        prompt: "A red square on white background",
        size: "256x256",
      });
      const details = { model, status_code: response.status, endpoint: endpoint.label, url: endpoint.url };
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
      return { ok: false, status_code: result.status_code, summary: "image generation failed", details: { endpoint: endpoint.label, url: endpoint.url, candidate_models: candidateModels, attempts: result.attempts } };
    }
    return {
      ok: true,
      status_code: result.status_code,
      summary: "image generation works",
      details: { ...result.details, attempts: result.attempts },
    };
  }

  async probeCapabilities() {
    const endpointSupport = {};
    const ensureEndpoint = (entry) => {
      if (!endpointSupport[entry.label]) {
        endpointSupport[entry.label] = {
          url: entry.url,
          kind: entry.kind,
          supported: false,
          text_supported: false,
          vision_supported: false,
        };
      }
      return endpointSupport[entry.label];
    };
    const models = [];
    for (const model of this.pickTextProbeModels()) {
      const perEndpoint = {};
      let textSupported = false;
      let lastStatus = null;
      let lastBody = "";
      for (const endpoint of this.endpointCandidates) {
        const body = endpoint.kind === "chat"
          ? { model, messages: [{ role: "user", content: "Reply with exactly: OK_TEXT" }], max_tokens: 12 }
          : endpoint.kind === "responses"
            ? { model, input: "Reply with exactly: OK_TEXT", max_output_tokens: 12 }
            : endpoint.kind === "embeddings"
              ? { model, input: "macro rotation" }
              : { model, prompt: "A red square on white background", size: "256x256" };
        const response = await this.request("POST", endpoint.url, body);
        const bodyText = summarizeErrorBody(await response.text());
        perEndpoint[endpoint.label] = {
          url: endpoint.url,
          status_code: response.status,
          text_supported: response.ok,
          vision_supported: false,
        };
        const info = ensureEndpoint(endpoint);
        if (response.ok) {
          textSupported = true;
          info.supported = true;
          info.text_supported = true;
        }
        lastStatus = response.status;
        lastBody = bodyText;
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

    for (const model of this.pickVisionProbeModels()) {
      const perEndpoint = {};
      let visionSupported = false;
      let lastStatus = null;
      let lastBody = "";
      for (const endpoint of this.endpointCandidates.filter((item) => item.kind === "chat" || item.kind === "responses")) {
        const body = endpoint.kind === "chat"
          ? {
            model,
            messages: [{
              role: "user",
              content: [
                { type: "text", text: "What color is this pixel? Reply with one word." },
                { type: "image_url", image_url: { url: "data:image/gif;base64,R0lGODlhAQABAIABAP///wAAACwAAAAAAQABAAACAkQBADs=" } },
              ],
            }],
            max_tokens: 16,
          }
          : {
            model,
            input: [{
              role: "user",
              content: [
                { type: "input_text", text: "What color is this pixel? Reply with one word." },
                { type: "input_image", image_url: "data:image/gif;base64,R0lGODlhAQABAIABAP///wAAACwAAAAAAQABAAACAkQBADs=" },
              ],
            }],
            max_output_tokens: 16,
          };
        const response = await this.request("POST", endpoint.url, body);
        const bodyText = summarizeErrorBody(await response.text());
        perEndpoint[endpoint.label] = {
          url: endpoint.url,
          status_code: response.status,
          text_supported: false,
          vision_supported: response.ok,
        };
        const info = ensureEndpoint(endpoint);
        if (response.ok) {
          visionSupported = true;
          info.supported = true;
          info.vision_supported = true;
        }
        lastStatus = response.status;
        lastBody = bodyText;
      }
      models.push({
        name: model,
        kind: "vision",
        ok: visionSupported,
        status_code: lastStatus,
        summary: visionSupported ? "vision probing passed" : "vision probing failed",
        details: {
          capabilities: { text: null, vision: visionSupported },
          endpoint_support: perEndpoint,
          last_error_body: visionSupported ? "" : lastBody,
        },
      });
    }
    return {
      ok: models.some((item) => item.ok),
      status_code: null,
      summary: "capabilities scan completed",
      details: {
        base_url: this.baseUrl,
        endpoint_candidates: this.endpointCandidates,
        endpoint_support: endpointSupport,
        models,
      },
    };
  }

  async probeDocs() {
    const endpoints = [];
    for (const path of ["/docs", "/openapi.json", "/health", "/version"]) {
      try {
        const response = await this.request("GET", `${this.baseUrl}${path}`);
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
      let url = hint;
      if (!/^https?:\/\//i.test(hint)) {
        const root = this.apiRoots[0] || this.baseUrl;
        const parsed = new URL(root);
        const fullPath = this.joinEndpoint(parsed.pathname.replace(/\/+$/, ""), hint);
        url = `${parsed.origin}${fullPath}`;
      }
      const entry = { path: hint, url };
      try {
        let response = await this.request("OPTIONS", url, null);
        entry.options_status = response.status;
        if (response.status >= 400) {
          response = await this.request("GET", url, null);
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
  const probeMode = String(payload.probe_mode || "quick");
  const endpointStrategy = String(payload.endpoint_strategy || "append");

  if (!baseUrl || !apiKey) {
    return json({ error: "Base URL 和 API Key 都是必填。" }, 400);
  }
  if (!/^https:\/\/[^/\s]+/i.test(baseUrl)) {
    return json({ error: "Cloudflare Pages 线上版只支持公网 https 地址。需要本地 http 调试时，请使用 Flask 版。" }, 400);
  }
  if (!Number.isFinite(timeout) || timeout < 1 || timeout > 120) {
    return json({ error: "Timeout must be between 1 and 120 seconds" }, 400);
  }

  const mergedProbes = endpointHints.length && !enabledProbes.includes("extra_endpoints")
    ? enabledProbes.concat(["extra_endpoints"])
    : enabledProbes;

  const prober = new GatewayProber(baseUrl, apiKey, timeout, {
    enabledProbes: mergedProbes,
    textModels,
    visionModels,
    endpointHints,
    probeMode,
    endpointStrategy,
  });
  const results = await prober.run();
  return json({ results, summary: summarizeResults(results) });
}
