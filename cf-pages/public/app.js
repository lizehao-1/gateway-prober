const form = document.getElementById("probe-form");
const submitButton = document.getElementById("submit-button");
const errorNode = document.getElementById("error");
const resultsPanel = document.getElementById("results-panel");
const summaryNode = document.getElementById("summary");
const cardsNode = document.getElementById("cards");
const cardTemplate = document.getElementById("card-template");
const progressNode = document.getElementById("progress");
const calloutNode = document.getElementById("callout");

function setLoading(isLoading) {
  submitButton.disabled = isLoading;
  submitButton.textContent = isLoading ? "检测中..." : "开始检测";
  progressNode.classList.toggle("hidden", !isLoading);
}

function renderSummary(baseUrl, results) {
  const passCount = results.filter((item) => item.ok).length;
  const failCount = results.length - passCount;
  summaryNode.innerHTML = "";
  [`Base URL: ${baseUrl}`, `通过: ${passCount}`, `失败: ${failCount}`].forEach((text) => {
    const chip = document.createElement("div");
    chip.className = "chip";
    chip.textContent = text;
    summaryNode.appendChild(chip);
  });
}

function renderCards(results) {
  cardsNode.innerHTML = "";
  for (const item of results) {
    const fragment = cardTemplate.content.cloneNode(true);
    fragment.querySelector(".name").textContent = item.name;
    const status = fragment.querySelector(".status");
    status.textContent = item.ok ? "PASS" : "FAIL";
    status.className = `status ${item.ok ? "ok" : "bad"}`;
    fragment.querySelector(".meta").textContent = `status=${item.status_code ?? "-"}, elapsed=${item.elapsed_ms}ms`;
    fragment.querySelector(".summary-text").textContent = item.summary;
    fragment.querySelector(".details").textContent = JSON.stringify(item.details ?? {}, null, 2);
    cardsNode.appendChild(fragment);
  }
}

function renderCallout(results) {
  const chatOk = results.find((item) => item.name === "chat_completions")?.ok;
  const toolOk = results.find((item) => item.name === "tool_calling")?.ok;
  const responsesOk = results.find((item) => item.name === "responses")?.ok;
  const embeddingsOk = results.find((item) => item.name === "embeddings")?.ok;

  const tips = [];
  if (chatOk && toolOk && responsesOk) {
    tips.push("这个网关适合文本型 agent、工具调用和新版 SDK。");
  } else if (chatOk) {
    tips.push("这个网关至少适合普通聊天和基础代码问答。");
  } else {
    tips.push("文本主接口没有完全测通，不建议直接接生产 IDE/Agent。");
  }

  if (embeddingsOk) {
    tips.push("Embeddings 可用，知识库、RAG、语义搜索类工作流也可以考虑。");
  } else {
    tips.push("Embeddings 不可用时，普通聊天通常还能用，但知识库问答、RAG、语义搜索、文档召回会受影响。");
  }

  calloutNode.textContent = tips.join(" ");
  calloutNode.classList.remove("hidden");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorNode.textContent = "";
  resultsPanel.classList.add("hidden");
  setLoading(true);

  const payload = {
    base_url: document.getElementById("base-url").value.trim(),
    api_key: document.getElementById("api-key").value.trim(),
    timeout: Number(document.getElementById("timeout").value),
  };

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
    renderSummary(payload.base_url, data.results);
    renderCallout(data.results);
    renderCards(data.results);
    resultsPanel.classList.remove("hidden");
  } catch (error) {
    errorNode.textContent = error.message || "检测失败";
    calloutNode.classList.add("hidden");
  } finally {
    setLoading(false);
  }
});
