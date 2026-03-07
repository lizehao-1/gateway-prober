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
  submitButton.textContent = isLoading ? "探测中..." : "开始探测";
  progressNode.classList.toggle("hidden", !isLoading);
}

function renderSummary(baseUrl, results) {
  const passCount = results.filter((item) => item.ok).length;
  const failCount = results.length - passCount;
  summaryNode.innerHTML = "";
  [`Base URL: ${baseUrl}`, `PASS: ${passCount}`, `FAIL: ${failCount}`].forEach((text) => {
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
  const required = ["chat_completions", "tool_calling", "responses"];
  const requiredOk = required.every((name) => results.find((item) => item.name === name)?.ok);
  const embeddingsOk = results.find((item) => item.name === "embeddings")?.ok;
  const imagesOk = results.find((item) => item.name === "images")?.ok;

  let text = "";
  if (requiredOk) {
    text = "这个网关适合文本型多智能体系统，`chat_completions`、`tool_calling`、`responses` 都可用。";
  } else {
    text = "这个网关不适合直接作为完整多智能体后端，至少有一项核心文本能力没有通过。";
  }

  if (!embeddingsOk) {
    text += " 当前不建议把 RAG / 向量检索层直接绑在这个网关上。";
  }
  if (imagesOk) {
    text += " 图片接口可用，可以额外承载图像生成场景。";
  }

  calloutNode.textContent = text;
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
      throw new Error(data.error || "探测失败");
    }
    renderSummary(payload.base_url, data.results);
    renderCallout(data.results);
    renderCards(data.results);
    resultsPanel.classList.remove("hidden");
  } catch (error) {
    errorNode.textContent = error.message || "探测失败";
    calloutNode.classList.add("hidden");
  } finally {
    setLoading(false);
  }
});
