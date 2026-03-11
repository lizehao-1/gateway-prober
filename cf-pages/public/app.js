const PROBE_OPTIONS = [
  { value: "models", label: "Models", description: "先获取模型列表，并给出文本、视觉、向量、图片候选排序。", checked: true },
  { value: "chat_completions", label: "Chat", description: "测试 /chat/completions 文本对话能力。", checked: true },
  { value: "tool_calling", label: "Tools", description: "测试 chat 接口的工具调用能力。", checked: true },
  { value: "responses", label: "Responses", description: "测试 /responses 或 /responses/compact。", checked: true },
  { value: "embeddings", label: "Embeddings", description: "测试 /embeddings 向量能力。", checked: true },
  { value: "images", label: "Images", description: "测试 /images/generations 图片生成能力。", checked: false },
  { value: "extra_endpoints", label: "Extra Endpoints", description: "测试你手填或预设追加的特殊端点。", checked: false },
  { value: "capabilities", label: "Capabilities", description: "按模型和端点做更细的文本/视觉能力扫描，最慢，但会额外生成较完整建议和报告。", checked: false },
  { value: "docs", label: "Docs", description: "探测 /docs、/openapi.json、/health、/version。", checked: false },
];

const ENDPOINT_PRESETS = [
  { value: "image_advanced", label: "图片编辑相关", description: "测 /v1/images/edits 和 /v1/images/variations", paths: ["/v1/images/edits", "/v1/images/variations"] },
  { value: "audio", label: "音频相关", description: "测 /v1/audio/transcriptions、/v1/audio/translations、/v1/audio/speech", paths: ["/v1/audio/transcriptions", "/v1/audio/translations", "/v1/audio/speech"] },
  { value: "moderation", label: "审核相关", description: "测 /v1/moderations", paths: ["/v1/moderations"] },
  { value: "legacy_edits", label: "旧版文本编辑", description: "测 /v1/edits", paths: ["/v1/edits"] },
  { value: "assistants", label: "Assistants 相关", description: "测 /v1/assistants、/v1/threads、/v1/threads/runs", paths: ["/v1/assistants", "/v1/threads", "/v1/threads/runs"] },
  { value: "files_batches", label: "文件与批处理", description: "测 /v1/files、/v1/uploads、/v1/batches", paths: ["/v1/files", "/v1/uploads", "/v1/batches"] },
  { value: "realtime", label: "Realtime 相关", description: "测 /v1/realtime", paths: ["/v1/realtime"] },
  { value: "fine_tuning", label: "微调相关", description: "测 /v1/fine_tuning/jobs", paths: ["/v1/fine_tuning/jobs"] },
];

const form = document.getElementById("probe-form");
const submitButton = document.getElementById("submit-button");
const errorNode = document.getElementById("error");
const noticeNode = document.getElementById("notice");
const estimateHint = document.getElementById("estimate-hint");
const progressNode = document.getElementById("progress");
const progressTitle = document.getElementById("progress-title");
const progressDetail = document.getElementById("progress-detail");
const resultsPanel = document.getElementById("results-panel");
const summaryNode = document.getElementById("summary-chips");
const rankingNode = document.getElementById("ranking-chips");
const calloutNode = document.getElementById("callout");
const reportPanel = document.getElementById("report-panel");
const reportBody = document.getElementById("report-body");
const copyReportButton = document.getElementById("copy-report-button");
const cardsNode = document.getElementById("results-cards");
const cardTemplate = document.getElementById("card-template");

function show(node, visible) {
  node.classList.toggle("hidden", !visible);
}

function escapeHtml(text) {
  return String(text).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

function parseTextareaList(text) {
  const trimmed = String(text || "").trim();
  if (!trimmed) {
    return [];
  }
  try {
    const parsed = JSON.parse(trimmed);
    if (Array.isArray(parsed)) {
      return parsed.map((item) => String(item).trim()).filter(Boolean);
    }
  } catch {}
  return trimmed.replaceAll("\r", "\n").replaceAll(",", "\n").split("\n").map((item) => item.trim()).filter(Boolean);
}

function selectedValues(name) {
  return Array.from(document.querySelectorAll(`input[name="${name}"]:checked`)).map((node) => node.value);
}

function presetPaths(values) {
  const selected = new Set(values);
  return ENDPOINT_PRESETS.flatMap((preset) => selected.has(preset.value) ? preset.paths : []);
}

function estimateSeconds(timeout, enabledCount, probeMode, extraCount) {
  const multiplier = probeMode === "deep" ? 1.7 : 1.0;
  const extraFactor = 1 + Math.min(extraCount, 10) * 0.06;
  return Math.max(3, Math.round(Math.min(timeout, 8) * Math.max(enabledCount, 1) * 0.32 * multiplier * extraFactor));
}

function renderCheckboxes(containerId, name, items) {
  const container = document.getElementById(containerId);
  container.innerHTML = "";
  for (const item of items) {
    const label = document.createElement("label");
    label.className = "check";
    label.title = item.description;
    label.innerHTML = `<input type="checkbox" name="${name}" value="${item.value}" ${item.checked ? "checked" : ""}><span>${item.label}</span>`;
    container.appendChild(label);
  }
}

function buildNotice(baseUrl, endpointPaths, endpointStrategy) {
  const text = String(baseUrl || "").trim();
  if (!text) {
    return "";
  }
  const lowered = text.toLowerCase();
  const suffixes = [
    "/chat/completions", "/responses", "/responses/compact", "/embeddings", "/images/generations",
    "/images/edits", "/images/variations", "/audio/transcriptions", "/audio/translations",
    "/audio/speech", "/moderations", "/assistants", "/threads", "/threads/runs", "/files",
    "/uploads", "/batches", "/realtime", "/fine_tuning/jobs",
  ];
  if (suffixes.some((suffix) => lowered.endsWith(suffix))) {
    return "这个 Base URL 看起来像某个具体接口。通常建议填写根地址，或到 /v1 为止，再把特殊端点放到 Endpoint Paths 或高级预设里。";
  }
  if (!text.includes("://")) {
    return "你填写的是 host:port 形式。线上站建议使用完整 https 地址。";
  }
  if (String(endpointPaths || "").trim() && endpointStrategy === "custom_only") {
    return "当前是 Custom Only：只测你手填路径和高级预设，不再测默认端点。";
  }
  if (String(endpointPaths || "").trim()) {
    return "当前是 Append：会保留默认端点，同时追加你手填路径和高级预设。";
  }
  return "很多兼容网关只在 /v1 下工作。遇到根地址测不出来时，直接写到 /v1 往往更稳。";
}

function setLoading(isLoading, estimateText = "") {
  submitButton.disabled = isLoading;
  submitButton.textContent = isLoading ? "检测中..." : "开始探测";
  progressTitle.textContent = isLoading ? "正在检测，请稍候。" : "";
  progressDetail.textContent = estimateText || "Cloudflare Pages 版会在完成后一次性返回结果。";
  show(progressNode, isLoading);
}

function renderSummary(baseUrl, payload) {
  const summary = payload.summary || {};
  summaryNode.innerHTML = "";
  [
    `Base URL: ${baseUrl}`,
    `PASS: ${summary.pass_count ?? 0}`,
    `FAIL: ${summary.fail_count ?? 0}`,
    `Total: ${summary.total_elapsed_ms ?? 0}ms`,
    `Avg: ${summary.avg_elapsed_ms ?? 0}ms`,
    `Slowest: ${summary.slowest_probe ?? "-"}`,
  ].forEach((text) => {
    const chip = document.createElement("div");
    chip.className = "chip";
    chip.textContent = text;
    summaryNode.appendChild(chip);
  });
}

function renderRankings(rankings) {
  const groups = [
    ["text", "文本优先"],
    ["vision", "视觉优先"],
    ["embeddings", "向量优先"],
    ["images", "图片优先"],
  ];
  const html = groups
    .filter(([key]) => Array.isArray(rankings?.[key]) && rankings[key].length)
    .map(([key, label]) => `<div class="chip">${label}: ${escapeHtml(rankings[key].join(" > "))}</div>`)
    .join("");
  rankingNode.innerHTML = html;
  show(rankingNode, Boolean(html));
}

function probeByName(results, name) {
  return (results || []).find((item) => item.name === name) || null;
}

function summarizeEndpointSupport(endpointSupport) {
  const entries = Object.entries(endpointSupport || {});
  return {
    supported: entries.filter(([, value]) => value?.supported).map(([key]) => key),
    unsupported: entries.filter(([, value]) => !value?.supported).map(([key]) => key),
  };
}

function extractCardSummary(item) {
  const details = item.details || {};
  const attempts = details.attempts || [];
  const lastAttempt = attempts.length ? attempts[attempts.length - 1] : null;
  const lastDetails = lastAttempt && typeof lastAttempt === "object" ? (lastAttempt.details || {}) : {};
  return {
    model: details.model || lastDetails.model || "-",
    endpoint: details.endpoint || details.url || "-",
    attemptCount: attempts.length,
  };
}

function renderCards(results) {
  cardsNode.innerHTML = "";
  for (const item of results || []) {
    const fragment = cardTemplate.content.cloneNode(true);
    const meta = extractCardSummary(item);
    fragment.querySelector(".name").textContent = item.name;
    const status = fragment.querySelector(".status");
    status.textContent = item.ok ? "PASS" : "FAIL";
    status.className = `status ${item.ok ? "ok" : "bad"}`;
    fragment.querySelector(".meta").textContent = `status=${item.status_code ?? "-"}, elapsed=${item.elapsed_ms}ms`;
    fragment.querySelector(".summary-text").textContent = item.summary || "";
    fragment.querySelector(".mini").textContent = `模型：${meta.model} | 尝试次数：${meta.attemptCount} | 端点：${meta.endpoint}`;
    fragment.querySelector(".details").textContent = JSON.stringify(item.details ?? {}, null, 2);
    cardsNode.appendChild(fragment);
  }
}

function renderCallout(results, payload) {
  const models = probeByName(results, "models")?.details?.rankings || {};
  const endpointSupport = probeByName(results, "capabilities")?.details?.endpoint_support || {};
  const endpointSummary = summarizeEndpointSupport(endpointSupport);
  const chatOk = probeByName(results, "chat_completions")?.ok || endpointSupport["/v1/chat/completions"]?.supported;
  const responsesOk = probeByName(results, "responses")?.ok || endpointSupport["/v1/responses"]?.supported;
  const toolsOk = probeByName(results, "tool_calling")?.ok;
  const embeddingsOk = probeByName(results, "embeddings")?.ok || endpointSupport["/v1/embeddings"]?.supported;
  const imagesOk = probeByName(results, "images")?.ok || endpointSupport["/v1/images/generations"]?.supported;

  const tips = [];
  if (chatOk && responsesOk) {
    tips.push("这个网关同时兼容 chat/completions 和 responses，接大多数 IDE/SDK 会更稳。");
  } else if (chatOk) {
    tips.push("这个网关至少适合普通聊天、代码问答和传统 chat/completions 客户端。");
  } else if (responsesOk) {
    tips.push("这个网关更偏新版 responses 风格，旧客户端可能不一定直接兼容。");
  } else {
    tips.push("文本主接口没有完全测通，不建议直接接生产 IDE 或 Agent。");
  }
  if (toolsOk) {
    tips.push("Tool calling 可用，适合自动化、函数调用和 agent 工作流。");
  }
  if (embeddingsOk) {
    tips.push("Embeddings 可用，知识库问答、RAG、语义搜索这类工作流也可以考虑。");
  } else {
    tips.push("Embeddings 不可用时，普通聊天通常还能用，但知识库问答、RAG、语义搜索、文档召回会受影响。");
  }
  if (imagesOk) {
    tips.push("图片生成接口可用。");
  }
  if (endpointSummary.supported.length) {
    tips.push(`细扫通过的端点有：${endpointSummary.supported.join("、")}。`);
  }
  if (Array.isArray(models.text) && models.text.length) {
    tips.push(`推荐优先尝试的文本模型：${models.text.slice(0, 3).join("、")}。`);
  }
  calloutNode.textContent = tips.join(" ");
  show(calloutNode, Boolean(tips.length));
}

function buildCapabilitiesReport(results) {
  const capabilities = probeByName(results, "capabilities")?.details;
  const modelRanks = probeByName(results, "models")?.details?.rankings || {};
  const lines = [];
  if (!capabilities) {
    return "";
  }
  const endpointSupport = capabilities.endpoint_support || {};
  const models = capabilities.models || [];
  const endpointSummary = summarizeEndpointSupport(endpointSupport);

  lines.push("Gateway 完整报告");
  lines.push("");
  lines.push(`Base URL: ${capabilities.base_url || "-"}`);
  lines.push("");
  lines.push("一、整体结论");
  lines.push(`- 已测通端点：${endpointSummary.supported.length ? endpointSummary.supported.join("、") : "无"}`);
  lines.push(`- 未测通端点：${endpointSummary.unsupported.length ? endpointSummary.unsupported.join("、") : "无"}`);
  if (Array.isArray(modelRanks.text) && modelRanks.text.length) {
    lines.push(`- 推荐优先尝试的文本模型：${modelRanks.text.slice(0, 5).join("、")}`);
  }
  if (Array.isArray(modelRanks.vision) && modelRanks.vision.length) {
    lines.push(`- 推荐优先尝试的视觉模型：${modelRanks.vision.slice(0, 3).join("、")}`);
  }
  if (Array.isArray(modelRanks.embeddings) && modelRanks.embeddings.length) {
    lines.push(`- 推荐优先尝试的 embedding 模型：${modelRanks.embeddings.slice(0, 3).join("、")}`);
  }
  lines.push("");
  lines.push("二、接入建议");
  lines.push(`- ${calloutNode.textContent || "未形成明确建议。"} `);
  lines.push("");
  lines.push("三、模型结论");
  for (const item of models.slice(0, 10)) {
    const okEndpoints = Object.entries(item.details?.endpoint_support || {})
      .filter(([, info]) => info.text_supported || info.vision_supported)
      .map(([key]) => `${key}(${infoText(item.details.endpoint_support[key].status_code)})`);
    const badEndpoints = Object.entries(item.details?.endpoint_support || {})
      .filter(([, info]) => !info.text_supported && !info.vision_supported)
      .map(([key]) => `${key}(${infoText(item.details.endpoint_support[key].status_code)})`);
    lines.push(`- ${item.name}：${item.ok ? "可用" : "不可用"}`);
    if (okEndpoints.length) lines.push(`  可用端点：${okEndpoints.join("、")}`);
    if (badEndpoints.length) lines.push(`  失败端点：${badEndpoints.join("、")}`);
  }
  lines.push("");
  lines.push("四、下一步建议");
  if (endpointSummary.supported.includes("/v1/chat/completions") && endpointSummary.supported.includes("/v1/responses")) {
    lines.push("- 这个网关同时兼容 chat/completions 和 responses，接 IDE 或 SDK 都比较稳。");
  } else if (endpointSummary.supported.includes("/v1/chat/completions")) {
    lines.push("- 更推荐接传统 chat/completions 客户端。");
  } else if (endpointSummary.supported.includes("/v1/responses")) {
    lines.push("- 更推荐接新版 responses 风格客户端。");
  }
  if (!endpointSummary.supported.includes("/v1/embeddings")) {
    lines.push("- Embeddings 未测通，不建议直接用于知识库问答、RAG、语义搜索。");
  }
  if (!endpointSummary.supported.includes("/v1/images/generations")) {
    lines.push("- 图片生成未测通，更适合文本场景。");
  }
  return lines.join("\n");
}

function infoText(value) {
  return value ?? "-";
}

function renderReport(results) {
  const report = buildCapabilitiesReport(results);
  reportBody.textContent = report;
  show(reportPanel, Boolean(report));
}

function updateEstimateHint() {
  const timeout = Number(document.getElementById("timeout").value || 20);
  const enabled = selectedValues("enabled_probes");
  const presets = selectedValues("endpoint_preset_groups");
  const extraCount = parseTextareaList(document.getElementById("endpoint-paths").value).length + presetPaths(presets).length;
  const probeMode = document.getElementById("probe-mode").value || "quick";
  const capabilityTip = enabled.includes("capabilities") ? " 已开启 Capabilities，完成后会附带较完整建议和报告。" : "";
  estimateHint.textContent = `预计耗时约 ${estimateSeconds(timeout, enabled.length, probeMode, extraCount)} 秒。勾选越多、路径越多，等待越久。${capabilityTip}`;
}

function collectPayload() {
  const endpointPresetGroups = selectedValues("endpoint_preset_groups");
  const enabledProbes = selectedValues("enabled_probes");
  const mergedEndpointPaths = parseTextareaList(document.getElementById("endpoint-paths").value).concat(presetPaths(endpointPresetGroups));
  return {
    base_url: document.getElementById("base-url").value.trim(),
    api_key: document.getElementById("api-key").value.trim(),
    timeout: Number(document.getElementById("timeout").value || 20),
    probe_mode: document.getElementById("probe-mode").value || "quick",
    endpoint_strategy: document.getElementById("endpoint-strategy").value || "append",
    endpoint_paths: JSON.stringify(Array.from(new Set(mergedEndpointPaths))),
    text_models: document.getElementById("text-models").value || "",
    vision_models: document.getElementById("vision-models").value || "",
    enabled_probes: enabledProbes,
    endpoint_preset_groups: endpointPresetGroups,
  };
}

document.addEventListener("DOMContentLoaded", () => {
  renderCheckboxes("probe-options", "enabled_probes", PROBE_OPTIONS);
  renderCheckboxes("preset-options", "endpoint_preset_groups", ENDPOINT_PRESETS.map((item) => ({ ...item, checked: false })));
  updateEstimateHint();

  form.addEventListener("change", updateEstimateHint);
  form.addEventListener("input", updateEstimateHint);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = collectPayload();
    const extraCount = parseTextareaList(payload.endpoint_paths).length + presetPaths(payload.endpoint_preset_groups).length;
    const estimate = estimateSeconds(payload.timeout, payload.enabled_probes.length, payload.probe_mode, extraCount);
    errorNode.textContent = "";
    noticeNode.textContent = buildNotice(payload.base_url, payload.endpoint_paths, payload.endpoint_strategy);
    show(noticeNode, Boolean(noticeNode.textContent));
    show(errorNode, false);
    show(resultsPanel, false);
    setLoading(true, `预计耗时约 ${estimate} 秒。当前是 Cloudflare Pages 版，结果会在检测结束后一次性返回。`);

    try {
      const response = await fetch("/api/probe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || "检测失败");
      }
      renderSummary(payload.base_url, data);
      renderRankings(data.summary?.rankings || {});
      renderCallout(data.results || [], data);
      renderReport(data.results || []);
      renderCards(data.results || []);
      show(resultsPanel, true);
    } catch (error) {
      errorNode.textContent = error.message || "检测失败";
      show(errorNode, true);
      show(calloutNode, false);
      show(reportPanel, false);
    } finally {
      setLoading(false);
    }
  });

  copyReportButton?.addEventListener("click", async () => {
    if (!reportBody.textContent) {
      return;
    }
    await navigator.clipboard.writeText(reportBody.textContent);
    copyReportButton.textContent = "已复制";
    setTimeout(() => {
      copyReportButton.textContent = "复制报告";
    }, 1200);
  });
});
