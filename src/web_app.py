from __future__ import annotations

import json
from typing import Any, Dict, List

from flask import Flask, render_template_string, request

from probe_gateway import DEFAULT_TIMEOUT, GatewayProber


app = Flask(__name__)


PAGE_TEMPLATE = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Gateway Prober</title>
  <style>
    :root {
      --bg: #f3efe6;
      --panel: #fffdf8;
      --ink: #1f2937;
      --muted: #6b7280;
      --line: #d6d0c4;
      --accent: #0f766e;
      --accent-2: #b45309;
      --ok: #166534;
      --bad: #b91c1c;
      --chip: #efe8da;
      --shadow: 0 18px 40px rgba(31, 41, 55, 0.08);
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans SC", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(15,118,110,0.12), transparent 28%),
        radial-gradient(circle at top right, rgba(180,83,9,0.10), transparent 24%),
        linear-gradient(180deg, #f7f3ec 0%, var(--bg) 100%);
      min-height: 100vh;
    }

    .shell {
      width: min(1120px, calc(100vw - 32px));
      margin: 32px auto;
      display: grid;
      gap: 20px;
    }

    .hero, .panel {
      background: rgba(255, 253, 248, 0.92);
      border: 1px solid rgba(214, 208, 196, 0.85);
      border-radius: 24px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(8px);
    }

    .hero {
      padding: 28px;
      display: grid;
      gap: 10px;
    }

    h1 {
      margin: 0;
      font-size: clamp(28px, 4vw, 44px);
      line-height: 1.05;
      letter-spacing: -0.03em;
    }

    .sub {
      margin: 0;
      color: var(--muted);
      font-size: 15px;
    }

    .panel {
      padding: 22px;
    }

    form {
      display: grid;
      gap: 16px;
    }

    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 14px;
    }

    label {
      display: grid;
      gap: 8px;
      font-weight: 600;
      font-size: 14px;
    }

    input {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 13px 14px;
      background: #fff;
      color: var(--ink);
      font-size: 14px;
    }

    input:focus {
      outline: 2px solid rgba(15,118,110,0.18);
      border-color: var(--accent);
    }

    .actions {
      display: flex;
      gap: 12px;
      align-items: center;
      flex-wrap: wrap;
    }

    button {
      border: 0;
      border-radius: 999px;
      padding: 13px 20px;
      background: linear-gradient(135deg, var(--accent), #155e75);
      color: white;
      font-size: 14px;
      font-weight: 700;
      cursor: pointer;
    }

    .hint {
      color: var(--muted);
      font-size: 13px;
    }

    .summary {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin-bottom: 16px;
    }

    .chip {
      background: var(--chip);
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 8px 12px;
      font-size: 13px;
    }

    .cards {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 14px;
    }

    .card {
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 16px;
      background: linear-gradient(180deg, rgba(255,255,255,0.95), rgba(251,248,242,0.95));
    }

    .topline {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      margin-bottom: 10px;
    }

    .status {
      font-size: 12px;
      font-weight: 700;
      border-radius: 999px;
      padding: 6px 10px;
    }

    .status.ok {
      background: rgba(22,101,52,0.10);
      color: var(--ok);
    }

    .status.bad {
      background: rgba(185,28,28,0.10);
      color: var(--bad);
    }

    .meta {
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 8px;
    }

    pre {
      margin: 0;
      overflow: auto;
      padding: 12px;
      border-radius: 14px;
      background: #1f2937;
      color: #f9fafb;
      font-size: 12px;
      line-height: 1.45;
    }

    .error {
      color: var(--bad);
      font-size: 14px;
      margin: 0;
    }
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <h1>Gateway Capability Probe</h1>
      <p class="sub">输入 OpenAI-compatible 网关的 URL 和 Key，自动探测模型列表、聊天、工具调用、Responses、Embeddings、图片能力和文档端点。</p>
    </section>

    <section class="panel">
      <form method="post">
        <div class="grid">
          <label>
            Base URL
            <input name="base_url" placeholder="https://example.com" value="{{ form.base_url }}">
          </label>
          <label>
            API Key
            <input name="api_key" type="password" placeholder="sk-..." value="{{ form.api_key }}">
          </label>
          <label>
            Timeout (seconds)
            <input name="timeout" type="number" min="1" max="120" value="{{ form.timeout }}">
          </label>
        </div>
        <div class="actions">
          <button type="submit">开始探测</button>
          <span class="hint">页面不会持久化保存 key；请求仅用于当前探测。</span>
        </div>
      </form>
      {% if error %}
      <p class="error">{{ error }}</p>
      {% endif %}
    </section>

    {% if results %}
    <section class="panel">
      <div class="summary">
        <div class="chip">Base URL: {{ form.base_url }}</div>
        <div class="chip">PASS: {{ pass_count }}</div>
        <div class="chip">FAIL: {{ fail_count }}</div>
      </div>
      <div class="cards">
        {% for item in results %}
        <article class="card">
          <div class="topline">
            <strong>{{ item.name }}</strong>
            <span class="status {{ 'ok' if item.ok else 'bad' }}">{{ 'PASS' if item.ok else 'FAIL' }}</span>
          </div>
          <div class="meta">status={{ item.status_code if item.status_code is not none else '-' }}, elapsed={{ item.elapsed_ms }}ms</div>
          <p>{{ item.summary }}</p>
          <pre>{{ item.details_json }}</pre>
        </article>
        {% endfor %}
      </div>
    </section>
    {% endif %}
  </main>
</body>
</html>
"""


def build_view_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for item in results:
      copy = dict(item)
      copy["details_json"] = json.dumps(item.get("details", {}), ensure_ascii=False, indent=2)
      items.append(copy)
    return items


@app.route("/", methods=["GET", "POST"])
def index() -> str:
    form = {"base_url": "", "api_key": "", "timeout": str(DEFAULT_TIMEOUT)}
    error = ""
    results: List[Dict[str, Any]] = []

    if request.method == "POST":
        form["base_url"] = request.form.get("base_url", "").strip()
        form["api_key"] = request.form.get("api_key", "").strip()
        form["timeout"] = request.form.get("timeout", str(DEFAULT_TIMEOUT)).strip() or str(DEFAULT_TIMEOUT)

        if not form["base_url"] or not form["api_key"]:
            error = "Base URL 和 API Key 都是必填。"
        else:
            try:
                timeout = int(form["timeout"])
                prober = GatewayProber(base_url=form["base_url"], api_key=form["api_key"], timeout=timeout)
                results = [item.to_dict() for item in prober.run()]
            except ValueError:
                error = "Timeout 必须是整数。"
            except Exception as exc:  # pragma: no cover
                error = str(exc)

    view_results = build_view_results(results)
    pass_count = sum(1 for item in results if item.get("ok"))
    fail_count = sum(1 for item in results if not item.get("ok"))

    return render_template_string(
        PAGE_TEMPLATE,
        form=form,
        error=error,
        results=view_results,
        pass_count=pass_count,
        fail_count=fail_count,
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False)
