from __future__ import annotations

import json
import sys
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from probe_gateway import DEFAULT_TIMEOUT, GatewayProber


app = Flask(__name__)

PAGE = """
<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Gateway Prober</title></head>
<body>
<h1>Gateway Capability Probe</h1>
<p>默认优先检测文本相关能力，不默认检测图片。</p>
<p><a href="/docs-page">查看详细说明</a></p>
<form method="post" action="/api/probe">
  <p>Base URL <input name="base_url"></p>
  <p>API Key <input name="api_key" type="password"></p>
  <p>Timeout <input name="timeout" type="number" value="20"></p>
  <p><button type="submit">开始检测</button></p>
</form>
</body>
</html>
"""

DOCS = """
<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Gateway Prober 详细说明</title></head>
<body>
<h1>Gateway Prober 详细说明</h1>
<p><a href="/">返回首页</a></p>
<h2>如果 embeddings 不可用，会影响什么</h2>
<p>embeddings 不是聊天模型，而是“向量模型”。它的作用是把文本变成向量，用于语义检索、知识库、RAG、相似内容匹配。</p>
<ul>
  <li>不影响普通聊天、代码问答、工具调用这类主要依赖 chat/responses 的能力。</li>
  <li>会影响知识库问答、RAG、语义搜索、文档召回、相似片段检索。</li>
</ul>
<h2>如果 opencode 或其他 IDE 用了不支持的后缀怎么办</h2>
<p>关键不是模型名，而是客户端实际打了哪个后缀。比如客户端固定调用 <code>/v1/chat/completions</code>，而网关只支持 <code>/v1/responses</code>，那就会直接不兼容。</p>
<ul>
  <li>如果客户端支持自定义后缀，就改成测通的那个后缀。</li>
  <li>如果客户端只让填 Base URL、不让改后缀，那就必须使用和它默认后缀兼容的网关。</li>
</ul>
</body>
</html>
"""


@app.get("/")
def index() -> str:
    return render_template_string(PAGE)


@app.get("/docs-page")
def docs_page() -> str:
    return render_template_string(DOCS)


@app.post("/api/probe")
def api_probe():
    payload = request.get_json(silent=True) or request.form
    base_url = str(payload.get("base_url", "")).strip()
    api_key = str(payload.get("api_key", "")).strip()
    timeout = int(payload.get("timeout") or DEFAULT_TIMEOUT)
    if not base_url or not api_key:
        return jsonify({"error": "Base URL 和 API Key 都是必填。"}), 400
    prober = GatewayProber(
        base_url=base_url,
        api_key=api_key,
        timeout=timeout,
        enabled_probes=["models", "chat_completions", "tool_calling", "responses", "embeddings"],
    )
    results = [item.to_dict() for item in prober.run()]
    return jsonify({"results": results})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False)
