from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

from flask import Flask, jsonify, render_template_string, request

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from probe_gateway import DEFAULT_PROBES, DEFAULT_TIMEOUT, GatewayProber, _to_json_safe


app = Flask(__name__)

PROBE_OPTIONS = [
    {"value": "models", "label": "Models", "description": "先获取模型列表，并给出文本、视觉、向量、图片候选排序。"},
    {"value": "chat_completions", "label": "Chat", "description": "测试 /chat/completions 文本对话能力。"},
    {"value": "tool_calling", "label": "Tools", "description": "测试 chat 接口的工具调用能力。"},
    {"value": "responses", "label": "Responses", "description": "测试 /responses 或 /responses/compact。"},
    {"value": "embeddings", "label": "Embeddings", "description": "测试 /embeddings 向量能力。"},
    {"value": "images", "label": "Images", "description": "测试 /images/generations 图片生成能力。"},
    {"value": "extra_endpoints", "label": "Extra Endpoints", "description": "测试你手填或预设追加的特殊端点。"},
    {"value": "capabilities", "label": "Capabilities", "description": "按模型和端点做更细的文本/视觉能力扫描，最慢。"},
    {"value": "docs", "label": "Docs", "description": "探测 /docs、/openapi.json、/health、/version。"},
]

DEFAULT_UI_PROBES = [item for item in DEFAULT_PROBES if item not in {"capabilities", "docs", "extra_endpoints"}]

ENDPOINT_PRESET_GROUPS = [
    {"value": "image_advanced", "label": "图片编辑相关", "description": "测 /v1/images/edits 和 /v1/images/variations", "paths": ["/v1/images/edits", "/v1/images/variations"]},
    {"value": "audio", "label": "音频相关", "description": "测 /v1/audio/transcriptions、/v1/audio/translations、/v1/audio/speech", "paths": ["/v1/audio/transcriptions", "/v1/audio/translations", "/v1/audio/speech"]},
    {"value": "moderation", "label": "审核相关", "description": "测 /v1/moderations", "paths": ["/v1/moderations"]},
    {"value": "legacy_edits", "label": "旧版文本编辑", "description": "测 /v1/edits", "paths": ["/v1/edits"]},
    {"value": "assistants", "label": "Assistants 相关", "description": "测 /v1/assistants、/v1/threads、/v1/threads/runs", "paths": ["/v1/assistants", "/v1/threads", "/v1/threads/runs"]},
    {"value": "files_batches", "label": "文件与批处理", "description": "测 /v1/files、/v1/uploads、/v1/batches", "paths": ["/v1/files", "/v1/uploads", "/v1/batches"]},
    {"value": "realtime", "label": "Realtime 相关", "description": "测 /v1/realtime", "paths": ["/v1/realtime"]},
    {"value": "fine_tuning", "label": "微调相关", "description": "测 /v1/fine_tuning/jobs", "paths": ["/v1/fine_tuning/jobs"]},
]

ENDPOINT_GUIDE = [
    {"path": "/v1/chat/completions", "purpose": "传统聊天接口", "request_shape": "messages[]", "notes": "很多旧客户端、编辑器插件、网关都还在用它。"},
    {"path": "/v1/responses", "purpose": "新式统一响应接口", "request_shape": "input", "notes": "新版 SDK 更常见，文本和多模态能力通常会往这里集中。"},
    {"path": "/v1/responses/compact", "purpose": "Responses 变体", "request_shape": "input", "notes": "有些兼容层只实现 compact 版本。"},
    {"path": "/v1/embeddings", "purpose": "向量生成", "request_shape": "input text", "notes": "用于知识库、检索、RAG，通常需要专用 embedding 模型。"},
    {"path": "/v1/images/generations", "purpose": "图片生成", "request_shape": "prompt", "notes": "返回 url 或 base64 图像数据。"},
    {"path": "/v1/images/edits", "purpose": "图片编辑", "request_shape": "image + prompt", "notes": "通常要求上传原图或遮罩。"},
    {"path": "/v1/images/variations", "purpose": "图片变体", "request_shape": "image", "notes": "基于原图生成相近版本。"},
    {"path": "/v1/audio/transcriptions", "purpose": "语音转文字", "request_shape": "audio file", "notes": "常见于 Whisper 兼容接口。"},
    {"path": "/v1/audio/translations", "purpose": "语音翻译", "request_shape": "audio file", "notes": "把音频转成另一种语言文本。"},
    {"path": "/v1/audio/speech", "purpose": "文字转语音", "request_shape": "text", "notes": "有的网关会单独实现。"},
    {"path": "/v1/moderations", "purpose": "内容审核", "request_shape": "text or image", "notes": "检测违规、敏感内容。"},
    {"path": "/v1/assistants", "purpose": "Assistants API", "request_shape": "assistant config", "notes": "较重，很多兼容网关并不实现。"},
    {"path": "/v1/threads", "purpose": "Assistants 会话线程", "request_shape": "thread messages", "notes": "通常和 assistants 配套。"},
    {"path": "/v1/threads/runs", "purpose": "Assistants 执行", "request_shape": "assistant + thread", "notes": "把 thread 跑起来。"},
    {"path": "/v1/files", "purpose": "文件上传/管理", "request_shape": "multipart file", "notes": "常见于 fine-tuning、assistants、batch。"},
    {"path": "/v1/uploads", "purpose": "分段上传", "request_shape": "multipart or chunk", "notes": "部分新式 OpenAI API 会用。"},
    {"path": "/v1/batches", "purpose": "批处理任务", "request_shape": "batch config", "notes": "适合异步大批量请求。"},
    {"path": "/v1/realtime", "purpose": "实时音视频/双向流", "request_shape": "websocket or session", "notes": "通常不是简单的 HTTP POST。"},
    {"path": "/v1/fine_tuning/jobs", "purpose": "微调任务", "request_shape": "training job", "notes": "很多代理层只暴露模型调用，不暴露微调。"},
]

PROBE_JOBS: Dict[str, Dict[str, Any]] = {}
PROBE_JOBS_LOCK = threading.Lock()


def _parse_textarea_list(value: str) -> List[str]:
    text = (value or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except Exception:
        pass
    parts = text.replace("\r", "\n").replace(",", "\n").split("\n")
    return [part.strip() for part in parts if part.strip()]


def _preset_paths_from_values(values: List[str]) -> List[str]:
    selected = set(values or [])
    paths: List[str] = []
    for preset in ENDPOINT_PRESET_GROUPS:
        if preset["value"] in selected:
            paths.extend(preset["paths"])
    return paths


def _estimate_seconds(timeout: int, enabled_count: int, probe_mode: str, extra_paths_count: int) -> int:
    multiplier = 1.7 if probe_mode == "deep" else 1.0
    extra_factor = 1 + min(extra_paths_count, 10) * 0.06
    return max(3, round(min(timeout, 8) * max(enabled_count, 1) * 0.32 * multiplier * extra_factor))


def _default_form() -> Dict[str, Any]:
    return {
        "base_url": "",
        "api_key": "",
        "timeout": str(DEFAULT_TIMEOUT),
        "probe_mode": "quick",
        "endpoint_strategy": "append",
        "endpoint_paths": "",
        "text_models": "",
        "vision_models": "",
        "enabled_probes": DEFAULT_UI_PROBES[:],
        "endpoint_preset_groups": [],
    }


def build_notice(base_url: str, endpoint_paths: str, endpoint_strategy: str) -> str:
    text = (base_url or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    specific_suffixes = [
        "/chat/completions", "/responses", "/responses/compact", "/embeddings", "/images/generations",
        "/images/edits", "/images/variations", "/audio/transcriptions", "/audio/translations",
        "/audio/speech", "/moderations", "/assistants", "/threads", "/threads/runs", "/files",
        "/uploads", "/batches", "/realtime", "/fine_tuning/jobs",
    ]
    for suffix in specific_suffixes:
        if lowered.endswith(suffix):
            return "这个 Base URL 看起来像某个具体接口，不像网关根地址。通常建议填写根地址，或到 /v1 为止，再把特殊端点放到 Endpoint Paths 或高级预设里。"
    if "://" not in text:
        return "你填写的是 host:port 形式，系统会自动按 http:// 处理。"
    if endpoint_paths.strip() and endpoint_strategy == "custom_only":
        return "当前是 Custom Only：只测你手填路径和高级预设，不再测默认端点。"
    if endpoint_paths.strip():
        return "当前是 Append：会保留默认端点，同时追加你手填路径和高级预设。"
    return "系统会优先尝试 /v1 前缀；如果某个网关只在 /v1 下工作，直接写到 /v1 往往更稳。"


def _summarize_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    pass_count = sum(1 for item in results if item.get("ok"))
    fail_count = sum(1 for item in results if not item.get("ok"))
    total_elapsed_ms = sum(int(item.get("elapsed_ms", 0)) for item in results)
    avg_elapsed_ms = int(total_elapsed_ms / len(results)) if results else 0
    slowest = max(results, key=lambda item: int(item.get("elapsed_ms", 0)), default=None)
    slowest_probe = f"{slowest.get('name')} ({slowest.get('elapsed_ms')}ms)" if slowest else "-"
    rankings = {}
    for item in results:
        if item.get("name") == "models":
            rankings = ((item.get("details") or {}).get("rankings") or {})
            break
    return {
        "pass_count": pass_count,
        "fail_count": fail_count,
        "total_elapsed_ms": total_elapsed_ms,
        "avg_elapsed_ms": avg_elapsed_ms,
        "slowest_probe": slowest_probe,
        "rankings": rankings,
    }


def _update_job(job_id: str, **updates: Any) -> None:
    with PROBE_JOBS_LOCK:
        job = PROBE_JOBS.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updated_at"] = time.time()


def _safe_job_payload(job: Dict[str, Any]) -> Dict[str, Any]:
    return _to_json_safe(job)


def _run_probe_job(job_id: str, payload: Dict[str, Any]) -> None:
    def progress_callback(event: Dict[str, Any]) -> None:
        _update_job(
            job_id,
            progress=event.get("progress", 0),
            current_stage=event.get("stage", ""),
            current_message=event.get("message", ""),
            current_meta=event.get("meta", {}),
            status="running",
        )

    def cancel_callback() -> bool:
        with PROBE_JOBS_LOCK:
            job = PROBE_JOBS.get(job_id) or {}
            return bool(job.get("cancel_requested"))

    try:
        prober = GatewayProber(
            base_url=payload["base_url"],
            api_key=payload["api_key"],
            timeout=payload["timeout"],
            endpoint_paths=payload["endpoint_paths"],
            text_models=payload["text_models"],
            vision_models=payload["vision_models"],
            probe_mode=payload["probe_mode"],
            enabled_probes=payload["enabled_probes"],
            endpoint_strategy=payload["endpoint_strategy"],
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )
        results = [_to_json_safe(item.to_dict()) for item in prober.run()]
        summary = _summarize_results(results)
        was_cancelled = bool(results and results[-1].get("summary") == "probe cancelled") or cancel_callback()
        _update_job(
            job_id,
            status="cancelled" if was_cancelled else "done",
            progress=100 if not was_cancelled else min(99, int(PROBE_JOBS.get(job_id, {}).get("progress", 0) or 0)),
            current_stage="cancelled" if was_cancelled else "done",
            current_message="检测已终止" if was_cancelled else "检测完成",
            results=results,
            summary=summary,
            error="",
        )
    except Exception as exc:  # pragma: no cover
        _update_job(
            job_id,
            status="error",
            progress=100,
            current_stage="error",
            current_message="检测失败",
            error=str(exc),
        )


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
    .shell { width: min(1280px, calc(100vw - 32px)); margin: 32px auto; display: grid; gap: 20px; }
    .hero, .panel {
      background: rgba(255, 253, 248, 0.92);
      border: 1px solid rgba(214, 208, 196, 0.85);
      border-radius: 24px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(8px);
    }
    .hero { padding: 28px; display: grid; gap: 10px; }
    h1 { margin: 0; font-size: clamp(28px, 4vw, 44px); line-height: 1.05; letter-spacing: -0.03em; }
    h2 { margin: 0 0 10px; }
    .sub { margin: 0; color: var(--muted); font-size: 15px; }
    .navrow { display: flex; gap: 14px; flex-wrap: wrap; }
    .navlink { color: var(--accent); font-size: 14px; text-decoration: none; font-weight: 700; }
    .panel { padding: 22px; }
    form { display: grid; gap: 16px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; }
    .grid-wide { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; }
    label { display: grid; gap: 8px; font-weight: 600; font-size: 14px; }
    input, textarea, select {
      width: 100%; border: 1px solid var(--line); border-radius: 14px; padding: 13px 14px;
      background: #fff; color: var(--ink); font-size: 14px; font-family: inherit;
    }
    textarea { min-height: 88px; resize: vertical; }
    input:focus, textarea:focus, select:focus { outline: 2px solid rgba(15,118,110,0.18); border-color: var(--accent); }
    .checks { display: flex; flex-wrap: wrap; gap: 10px; }
    .check {
      display: inline-flex; align-items: center; gap: 8px; padding: 10px 12px;
      border: 1px solid var(--line); border-radius: 999px; background: #fff; font-weight: 500;
    }
    .check input { width: auto; margin: 0; }
    .actions { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
    button {
      border: 0; border-radius: 999px; padding: 13px 20px; background: linear-gradient(135deg, var(--accent), #155e75);
      color: white; font-size: 14px; font-weight: 700; cursor: pointer;
    }
    button.secondary { background: linear-gradient(135deg, #7c2d12, #b45309); }
    button:disabled { opacity: 0.6; cursor: not-allowed; }
    .hint { color: var(--muted); font-size: 13px; }
    .notice {
      margin: 0; padding: 12px 14px; border-radius: 14px; background: rgba(15,118,110,0.08);
      border: 1px solid rgba(15,118,110,0.18); color: #115e59; font-size: 14px;
    }
    .summary, .rankings { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
    .chip { background: var(--chip); border: 1px solid var(--line); border-radius: 999px; padding: 8px 12px; font-size: 13px; }
    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; }
    .card { border: 1px solid var(--line); border-radius: 18px; padding: 16px; background: linear-gradient(180deg, rgba(255,255,255,0.95), rgba(251,248,242,0.95)); }
    .topline { display: flex; justify-content: space-between; gap: 12px; align-items: center; margin-bottom: 10px; }
    .status { font-size: 12px; font-weight: 700; border-radius: 999px; padding: 6px 10px; }
    .status.ok { background: rgba(22,101,52,0.10); color: var(--ok); }
    .status.bad { background: rgba(185,28,28,0.10); color: var(--bad); }
    .meta { color: var(--muted); font-size: 12px; margin-bottom: 8px; }
    details { margin-top: 10px; }
    summary { cursor: pointer; color: var(--accent); font-size: 13px; font-weight: 700; list-style: none; }
    summary::-webkit-details-marker { display: none; }
    pre {
      margin: 0; overflow: auto; padding: 12px; border-radius: 14px; background: #1f2937;
      color: #f9fafb; font-size: 12px; line-height: 1.45;
    }
    .progressbar { width: 100%; height: 10px; background: rgba(214, 208, 196, 0.75); border-radius: 999px; overflow: hidden; }
    .progressbar > span { display: block; height: 100%; background: linear-gradient(90deg, var(--accent), #155e75); width: 0%; transition: width .25s ease; }
    .error { color: var(--bad); font-size: 14px; margin: 0; }
    .hidden { display: none; }
    .mini { font-size: 12px; color: var(--muted); }
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <h1>Gateway Capability Probe</h1>
      <p class="sub">输入兼容 OpenAI 的网关地址和 Key，自动检测常见端点、可用模型、能力强弱和常见兼容问题。</p>
      <div class="navrow">
        <a class="navlink" href="/docs-page">查看详细说明</a>
      </div>
    </section>

    <section class="panel">
      <form id="probeForm">
        <div class="grid">
          <label>Base URL
            <input name="base_url" placeholder="http://127.0.0.1:8000 或 https://example.com/v1" value="{{ form.base_url }}">
          </label>
          <label>API Key
            <input name="api_key" type="password" placeholder="sk-...">
          </label>
          <label>Timeout (seconds)
            <input name="timeout" type="number" min="1" max="120" value="{{ form.timeout }}">
          </label>
          <label>Probe Mode
            <select name="probe_mode">
              <option value="quick">Quick（常见端点，速度优先）</option>
              <option value="deep">Deep（更多前缀，兼容优先）</option>
            </select>
          </label>
          <label>Endpoint Strategy
            <select name="endpoint_strategy">
              <option value="append">Append（保留默认端点，再追加你写的路径）</option>
              <option value="custom_only">Custom Only（只测你写的路径和预设）</option>
            </select>
          </label>
        </div>
        <div class="grid-wide">
          <label>Endpoint Paths
            <textarea name="endpoint_paths" placeholder="/v1/responses, /v1/chat/completions&#10;/v1/images/edits, /v1/audio/translations&#10;也支持 JSON 数组"></textarea>
          </label>
          <label>Text Test Models
            <textarea name="text_models" placeholder="留空时会优先从 /models 里按主力文本模型排序挑选"></textarea>
          </label>
          <label>Vision Test Models
            <textarea name="vision_models" placeholder="留空时会优先从 /models 里按主力视觉模型排序挑选"></textarea>
          </label>
        </div>
        <label>Enabled Probes
          <div class="checks">
            {% for probe in probe_options %}
            <label class="check" title="{{ probe.description }}"><input type="checkbox" name="enabled_probes" value="{{ probe.value }}" {% if probe.value in form.enabled_probes %}checked{% endif %}><span>{{ probe.label }}</span></label>
            {% endfor %}
          </div>
          <span class="hint">默认先测最常用的文本能力，Images 默认关闭，避免首次探测过慢。Capabilities 最值得在准备正式接入时打开，因为它会补充模型级细扫、接入建议和完整报告。手填路径或高级预设时，Extra Endpoints 会自动加入。</span>
        </label>
        <label>高级预设
          <div class="checks">
            {% for preset in endpoint_presets %}
            <label class="check" title="{{ preset.description }}"><input type="checkbox" name="endpoint_preset_groups" value="{{ preset.value }}"><span>{{ preset.label }}</span></label>
            {% endfor %}
          </div>
        </label>
        <div class="actions">
          <button type="submit" id="submitButton">开始探测</button>
          <button type="button" id="cancelButton" class="secondary hidden">终止检测</button>
          <span class="hint">检测中会实时显示阶段、正在尝试的模型或端点。结果页会先给整体总结；如果开启 Capabilities，还会追加更完整的决策报告。</span>
        </div>
      </form>
      <p id="notice" class="notice hidden"></p>
      <p id="error" class="error hidden"></p>
    </section>

    <section class="panel hidden" id="progressPanel">
      <div class="summary">
        <div class="chip" id="progressUrl">Base URL: -</div>
        <div class="chip" id="progressEstimate">预计耗时: -</div>
        <div class="chip" id="progressStage">当前阶段: -</div>
        <div class="chip" id="progressMeta">当前目标: -</div>
      </div>
      <div class="progressbar"><span id="progressBar"></span></div>
      <p class="hint" id="progressMessage">等待开始</p>
    </section>

    <section class="panel hidden" id="resultsPanel">
      <div class="summary" id="summaryChips"></div>
      <div class="rankings" id="rankingChips"></div>
      <div class="card hidden" id="summaryPanel">
        <div class="topline">
          <strong>整体总结</strong>
          <span class="hint" id="selectionHint"></span>
        </div>
        <p class="summary-text" id="adviceBody"></p>
      </div>
      <div class="card hidden" id="reportPanel">
        <div class="topline">
          <strong>Capabilities 报告</strong>
          <button type="button" id="copyReportButton">复制报告</button>
        </div>
        <pre id="reportBody"></pre>
      </div>
      <div class="cards" id="resultsCards"></div>
    </section>
  </main>
  <script>
    const endpointPresets = {{ endpoint_presets_json|safe }};
    let currentJobId = null;

    function estimateSeconds(timeout, enabledCount, probeMode, extraCount) {
      const multiplier = probeMode === 'deep' ? 1.7 : 1.0;
      const extraFactor = 1 + Math.min(extraCount, 10) * 0.06;
      return Math.max(3, Math.round(Math.min(timeout, 8) * Math.max(enabledCount, 1) * 0.32 * multiplier * extraFactor));
    }

    function presetPaths(values) {
      const selected = new Set(values);
      return endpointPresets.flatMap(preset => selected.has(preset.value) ? preset.paths : []);
    }

    function show(el, yes) {
      if (!el) return;
      el.classList.toggle('hidden', !yes);
    }

    function renderNotice(text) {
      const el = document.getElementById('notice');
      if (!text) {
        show(el, false);
        return;
      }
      el.textContent = text;
      show(el, true);
    }

    function renderError(text) {
      const el = document.getElementById('error');
      if (!text) {
        show(el, false);
        return;
      }
      el.textContent = text;
      show(el, true);
    }

    function escapeHtml(text) {
      return String(text).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
    }

    function extractCardSummary(item) {
      const details = item.details || {};
      const attempts = details.attempts || [];
      const lastAttempt = attempts.length ? attempts[attempts.length - 1] : null;
      const model = details.model || (lastAttempt && lastAttempt.details && lastAttempt.details.model) || '-';
      const endpoint = details.endpoint || details.url || '-';
      return {model, endpoint, attemptCount: attempts.length};
    }

    function renderRankings(rankings) {
      const el = document.getElementById('rankingChips');
      const groups = [['text', '文本优先'], ['vision', '视觉优先'], ['embeddings', '向量优先'], ['images', '图片优先']];
      const html = groups
        .filter(([key]) => Array.isArray(rankings[key]) && rankings[key].length)
        .map(([key, label]) => `<div class="chip">${label}: ${escapeHtml(rankings[key].join(' > '))}</div>`)
        .join('');
      el.innerHTML = html;
      show(el, Boolean(html));
    }

    function probeByName(results, name) {
      return (results || []).find(item => item.name === name) || null;
    }

    function summarizeEndpointSupport(endpointSupport) {
      const entries = Object.entries(endpointSupport || {});
      return {
        supported: entries.filter(([, value]) => value && value.supported).map(([key]) => key),
        unsupported: entries.filter(([, value]) => !value || !value.supported).map(([key]) => key),
      };
    }

    function summarizeExtraEndpoints(results) {
      const endpoints = probeByName(results, 'extra_endpoints')?.details?.endpoints || [];
      const okCount = endpoints.filter(item => {
        const statuses = [item.options_status, item.get_status].filter(value => Number.isFinite(value));
        return statuses.some(value => value < 500);
      }).length;
      return { total: endpoints.length, okCount };
    }

    function summarizeDocs(results) {
      const endpoints = probeByName(results, 'docs')?.details?.endpoints || [];
      const okCount = endpoints.filter(item => Number.isFinite(item.status_code) && item.status_code < 500).length;
      return { total: endpoints.length, okCount };
    }

    function formatCapabilityModel(model) {
      const endpointSupport = model.details?.endpoint_support || {};
      const passed = Object.entries(endpointSupport)
        .filter(([, info]) => info && (info.text_supported || info.vision_supported))
        .map(([key, info]) => `${key}(${info.status_code ?? '-'})`);
      const failed = Object.entries(endpointSupport)
        .filter(([, info]) => info && !info.text_supported && !info.vision_supported)
        .map(([key, info]) => `${key}(${info.status_code ?? '-'})`);
      const lines = [`- ${model.name}：${model.ok ? '可用' : '暂不稳定'}`];
      if (passed.length) lines.push(`  可用端点：${passed.join('、')}`);
      if (failed.length) lines.push(`  失败端点：${failed.join('、')}`);
      if (model.details?.last_error_body) lines.push(`  最近报错：${model.details.last_error_body}`);
      return lines;
    }

    function buildAdvice(results) {
      const capabilities = probeByName(results, 'capabilities')?.details || null;
      const rankings = probeByName(results, 'models')?.details?.rankings || {};
      const endpointSupport = capabilities?.endpoint_support || {};
      const summary = summarizeEndpointSupport(endpointSupport);
      const extraSummary = summarizeExtraEndpoints(results);
      const docsSummary = summarizeDocs(results);
      const chatOk = Boolean(probeByName(results, 'chat_completions')?.ok || endpointSupport['/v1/chat/completions']?.supported);
      const responsesOk = Boolean(probeByName(results, 'responses')?.ok || endpointSupport['/v1/responses']?.supported);
      const toolsOk = Boolean(probeByName(results, 'tool_calling')?.ok);
      const embeddingsOk = Boolean(probeByName(results, 'embeddings')?.ok || endpointSupport['/v1/embeddings']?.supported);
      const imagesOk = Boolean(probeByName(results, 'images')?.ok || endpointSupport['/v1/images/generations']?.supported);
      const tips = [];

      if (chatOk && responsesOk) {
        tips.push('文本主接口同时兼容 chat/completions 和 responses，接老客户端和新 SDK 都比较稳。');
      } else if (chatOk) {
        tips.push('当前更适合接传统 chat/completions 生态，很多 IDE 和旧 SDK 会更稳。');
      } else if (responsesOk) {
        tips.push('当前更偏新版 responses 风格，接新 SDK 或 agent workflow 会更顺手。');
      } else {
        tips.push('文本主接口没有完全测通，建议先不要直接上生产。');
      }
      if (toolsOk) {
        tips.push('Tool calling 可用，自动化编排、函数调用和 agent 工作流可以重点考虑。');
      }
      if (embeddingsOk) {
        tips.push('Embeddings 可用，RAG、语义检索和知识库问答可以继续评估。');
      } else {
        tips.push('Embeddings 不通时，普通聊天通常还能用，但知识库检索类场景要谨慎。');
      }
      if (imagesOk) {
        tips.push('图片生成接口已测通，适合海报、封面和视觉素材场景。');
      }
      if (Array.isArray(rankings.text) && rankings.text.length) {
        tips.push(`建议优先从这些文本模型试起：${rankings.text.slice(0, 3).join('、')}。`);
      }
      if (summary.supported.length) {
        tips.push(`已细扫通过的端点有：${summary.supported.join('、')}。`);
      }
      if (extraSummary.total) {
        tips.push(`额外端点共检查 ${extraSummary.total} 个，其中 ${extraSummary.okCount} 个有响应。`);
      }
      if (docsSummary.total) {
        tips.push(`文档/健康检查端点共检查 ${docsSummary.total} 个，其中 ${docsSummary.okCount} 个有响应。`);
      }
      return tips.join(' ');
    }

    function buildSelectionHint(results) {
      const hasCapabilities = Boolean(probeByName(results, 'capabilities'));
      const hasDocs = Boolean(probeByName(results, 'docs'));
      const hasExtra = Boolean(probeByName(results, 'extra_endpoints'));
      if (hasCapabilities) {
        return '已开启 Capabilities，以下结论包含模型级细扫。';
      }
      if (hasDocs || hasExtra) {
        return '未开启 Capabilities，以下结论基于主接口加补充端点探测。';
      }
      return '快速总结模式，适合先判断这个网关能不能接。';
    }

    function buildCapabilitiesReport(results, baseUrl) {
      const capabilities = probeByName(results, 'capabilities')?.details || null;
      if (!capabilities) {
        return '';
      }
      const rankings = probeByName(results, 'models')?.details?.rankings || {};
      const endpointSummary = summarizeEndpointSupport(capabilities.endpoint_support || {});
      const lines = [];
      lines.push('Gateway Capabilities 报告');
      lines.push('');
      lines.push(`Base URL: ${capabilities.base_url || baseUrl || '-'}`);
      lines.push('');
      lines.push('一、整体判断');
      lines.push(`- 已测通端点：${endpointSummary.supported.length ? endpointSummary.supported.join('、') : '无'}`);
      lines.push(`- 未测通端点：${endpointSummary.unsupported.length ? endpointSummary.unsupported.join('、') : '无'}`);
      if (Array.isArray(rankings.text) && rankings.text.length) {
        lines.push(`- 推荐优先尝试的文本模型：${rankings.text.slice(0, 5).join('、')}`);
      }
      if (Array.isArray(rankings.vision) && rankings.vision.length) {
        lines.push(`- 推荐优先尝试的视觉模型：${rankings.vision.slice(0, 3).join('、')}`);
      }
      if (Array.isArray(rankings.embeddings) && rankings.embeddings.length) {
        lines.push(`- 推荐优先尝试的向量模型：${rankings.embeddings.slice(0, 3).join('、')}`);
      }
      lines.push('');
      lines.push('二、接入建议');
      lines.push(`- ${buildAdvice(results) || '未形成明确建议。'}`);
      lines.push('');
      lines.push('三、模型结论');
      for (const model of (capabilities.models || []).slice(0, 12)) {
        lines.push(...formatCapabilityModel(model));
      }
      lines.push('');
      lines.push('四、下一步建议');
      lines.push('- 如果你要接 IDE 或聊天助手，先用报告里优先级最高的文本模型。');
      lines.push('- 如果你要接 RAG，先确认 /v1/embeddings 稳定返回向量，再做索引。');
      lines.push('- 如果你要接图片工作流，再单独复测 images 或高级预设里的图片相关端点。');
      return lines.join('\\n');
    }

    function renderAdviceAndReport(results, baseUrl) {
      const summaryPanel = document.getElementById('summaryPanel');
      const adviceNode = document.getElementById('adviceBody');
      const selectionHint = document.getElementById('selectionHint');
      const reportPanel = document.getElementById('reportPanel');
      const reportBody = document.getElementById('reportBody');
      const advice = buildAdvice(results);
      const report = buildCapabilitiesReport(results, baseUrl);
      selectionHint.textContent = buildSelectionHint(results);
      adviceNode.textContent = advice;
      reportBody.textContent = report;
      show(summaryPanel, Boolean(advice));
      show(reportPanel, Boolean(report));
    }

    function renderResults(payload, baseUrl) {
      const panel = document.getElementById('resultsPanel');
      const chips = document.getElementById('summaryChips');
      const cards = document.getElementById('resultsCards');
      const summary = payload.summary || {};
      chips.innerHTML = `
        <div class="chip">Base URL: ${escapeHtml(baseUrl)}</div>
        <div class="chip">PASS: ${summary.pass_count ?? 0}</div>
        <div class="chip">FAIL: ${summary.fail_count ?? 0}</div>
        <div class="chip">Total: ${summary.total_elapsed_ms ?? 0}ms</div>
        <div class="chip">Avg: ${summary.avg_elapsed_ms ?? 0}ms</div>
        <div class="chip">Slowest: ${escapeHtml(summary.slowest_probe ?? '-')}</div>
      `;
      renderRankings(summary.rankings || {});
      renderAdviceAndReport(payload.results || [], baseUrl);
      cards.innerHTML = (payload.results || []).map(item => {
        const card = extractCardSummary(item);
        return `
          <article class="card">
            <div class="topline">
              <strong>${escapeHtml(item.name)}</strong>
              <span class="status ${item.ok ? 'ok' : 'bad'}">${item.ok ? 'PASS' : 'FAIL'}</span>
            </div>
            <div class="meta">status=${item.status_code ?? '-'}, elapsed=${item.elapsed_ms}ms</div>
            <p>${escapeHtml(item.summary)}</p>
            <div class="mini">模型：${escapeHtml(card.model)} | 尝试次数：${card.attemptCount} | 端点：${escapeHtml(card.endpoint)}</div>
            <details>
              <summary>查看详情</summary>
              <pre>${escapeHtml(JSON.stringify(item.details || {}, null, 2))}</pre>
            </details>
          </article>
        `;
      }).join('');
      show(panel, true);
    }

    async function cancelJob() {
      if (!currentJobId) return;
      const res = await fetch(`/api/probe/cancel/${currentJobId}`, {method: 'POST'});
      const data = await res.json();
      renderNotice(data.message || '已请求终止');
    }

    async function pollJob(jobId, baseUrl) {
      const progressPanel = document.getElementById('progressPanel');
      const progressBar = document.getElementById('progressBar');
      const progressMessage = document.getElementById('progressMessage');
      const progressStage = document.getElementById('progressStage');
      const progressEstimate = document.getElementById('progressEstimate');
      const progressUrl = document.getElementById('progressUrl');
      const progressMeta = document.getElementById('progressMeta');
      const submitButton = document.getElementById('submitButton');
      const cancelButton = document.getElementById('cancelButton');
      progressUrl.textContent = `Base URL: ${baseUrl}`;
      show(progressPanel, true);
      show(cancelButton, true);
      while (true) {
        const res = await fetch(`/api/probe/status/${jobId}`);
        const data = await res.json();
        progressBar.style.width = `${data.progress || 0}%`;
        progressMessage.textContent = data.current_message || '正在检测...';
        progressStage.textContent = `当前阶段: ${data.current_stage || '-'}`;
        progressEstimate.textContent = `预计耗时: ${data.estimated_seconds || '-'} 秒`;
        const meta = data.current_meta || {};
        progressMeta.textContent = `当前目标: ${meta.model || meta.path || '-'}`;
        if (data.status === 'done') {
          currentJobId = null;
          submitButton.disabled = false;
          show(cancelButton, false);
          renderError('');
          renderResults(data, baseUrl);
          break;
        }
        if (data.status === 'cancelled') {
          currentJobId = null;
          submitButton.disabled = false;
          show(cancelButton, false);
          renderNotice('检测已终止，你可以调整勾选项后重新探测。');
          renderResults(data, baseUrl);
          break;
        }
        if (data.status === 'error') {
          currentJobId = null;
          submitButton.disabled = false;
          show(cancelButton, false);
          renderError(data.error || '检测失败');
          break;
        }
        await new Promise(resolve => setTimeout(resolve, 700));
      }
    }

    document.addEventListener('DOMContentLoaded', () => {
      document.getElementById('cancelButton').addEventListener('click', cancelJob);
      document.getElementById('copyReportButton').addEventListener('click', async function () {
        const reportBody = document.getElementById('reportBody');
        if (!reportBody.textContent) return;
        await navigator.clipboard.writeText(reportBody.textContent);
        this.textContent = '已复制';
        setTimeout(() => {
          this.textContent = '复制报告';
        }, 1200);
      });
      document.getElementById('probeForm').addEventListener('submit', async function (event) {
        event.preventDefault();
        renderError('');
        show(document.getElementById('resultsPanel'), false);
        const submitButton = document.getElementById('submitButton');
        submitButton.disabled = true;
        const form = new FormData(event.currentTarget);
        const endpointPresetGroups = form.getAll('endpoint_preset_groups');
        const enabledProbes = form.getAll('enabled_probes');
        const endpointPaths = (form.get('endpoint_paths') || '').trim();
        const baseUrl = (form.get('base_url') || '').trim();
        const endpointStrategy = (form.get('endpoint_strategy') || 'append').trim();
        const extraPaths = presetPaths(endpointPresetGroups);

        const noticeResp = await fetch('/api/probe/notice', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({base_url: baseUrl, endpoint_paths: endpointPaths, endpoint_strategy: endpointStrategy})
        });
        const noticeData = await noticeResp.json();
        renderNotice(noticeData.notice || '');

        const startResp = await fetch('/api/probe/start', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            base_url: baseUrl,
            api_key: form.get('api_key') || '',
            timeout: Number(form.get('timeout') || 20),
            probe_mode: form.get('probe_mode') || 'quick',
            endpoint_strategy: endpointStrategy,
            endpoint_paths: endpointPaths,
            text_models: form.get('text_models') || '',
            vision_models: form.get('vision_models') || '',
            enabled_probes: enabledProbes,
            endpoint_preset_groups: endpointPresetGroups
          })
        });
        const startData = await startResp.json();
        if (!startResp.ok) {
          submitButton.disabled = false;
          renderError(startData.error || '启动检测失败');
          return;
        }
        currentJobId = startData.job_id;
        document.getElementById('progressEstimate').textContent = `预计耗时: ${estimateSeconds(Number(form.get('timeout') || 20), enabledProbes.length, form.get('probe_mode') || 'quick', extraPaths.length)} 秒`;
        await pollJob(startData.job_id, baseUrl);
      });
    });
  </script>
</body>
</html>
"""


DOCS_TEMPLATE = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Gateway Prober Docs</title>
  <style>
    body { font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans SC", sans-serif; margin: 0; background: #f7f3ec; color: #1f2937; }
    main { width: min(980px, calc(100vw - 32px)); margin: 32px auto; background: #fffdf8; border: 1px solid #d6d0c4; border-radius: 24px; padding: 28px; }
    h1, h2 { margin-top: 0; }
    p, li { line-height: 1.7; }
    code, pre { font-family: Consolas, monospace; }
    pre { background: #1f2937; color: #f9fafb; padding: 14px; border-radius: 14px; overflow: auto; }
    a { color: #0f766e; }
    table { width: 100%; border-collapse: collapse; margin: 16px 0; }
    th, td { border: 1px solid #d6d0c4; padding: 10px; text-align: left; vertical-align: top; }
    th { background: #f3efe6; }
  </style>
</head>
<body>
  <main>
    <h1>Gateway Prober 详细说明</h1>
    <p><a href="/">返回检测页</a></p>
    <h2>1. 现在的检测顺序是什么</h2>
    <p>按你勾选的项目顺序检测。默认顺序是 Models、Chat、Tools、Responses、Embeddings，Images 默认关闭，避免第一次探测就过慢。Capabilities 和 Docs 默认关闭，因为它们更细也更慢；但如果你勾上 Capabilities，页面会额外生成更完整的接入建议、模型判断和可复制报告。</p>
    <p>如果成功拿到 /models，系统会先把模型按用途排序，再优先拿更像主力的模型去测；不是只死盯一个默认模型。某个模型失败时，会自动换下一个候选。</p>
    <h2>2. 为什么有时只写根地址测不到，写到 /v1 才行</h2>
    <p>不少兼容网关实际上只在 <code>/v1</code> 下暴露接口。理论上工具会尝试根地址和 /v1，但有些网关的转发、重写或防火墙规则只允许 <code>/v1/*</code>。遇到这种情况，直接把 Base URL 写成到 <code>/v1</code> 为止会更稳。</p>
    <h2>3. 如果 Embeddings 不可用，会影响什么</h2>
    <p><code>/v1/embeddings</code> 不是聊天接口，而是把文本转成向量。它做不了时，普通聊天、代码问答、工具调用通常仍然可以用，但依赖“向量检索”的功能会变弱，甚至直接不可用。</p>
    <ul>
      <li>通常还能做：普通对话、代码补全、代码解释、Agent 调工具、基于 <code>/chat/completions</code> 或 <code>/responses</code> 的常规 IDE 助手。</li>
      <li>通常会受影响：知识库问答、RAG、项目语义搜索、文档召回、相似内容匹配、先检索再回答的工作流。</li>
      <li>如果你的 IDE 或客户端主要靠聊天接口工作，它往往还能正常用；如果它严重依赖 embedding 做索引或检索，体验就会明显下降。</li>
    </ul>
    <h2>4. Quick / Deep 是什么</h2>
    <p><strong>Quick</strong> 只测更常见的前缀，速度优先。<strong>Deep</strong> 会增加更多前缀变体，例如 <code>/openai/v1</code>、<code>/api/v1</code>、<code>/api/openai/v1</code>，兼容更广，但会慢一些。</p>
    <h2>5. Endpoint Strategy 是什么</h2>
    <p><strong>Append</strong> 表示保留默认候选端点，再追加你手填的 Endpoint Paths 和高级预设。<strong>Custom Only</strong> 表示只测你手填和预设里的路径，不再测默认端点。</p>
    <h2>6. 常见后缀分别是干什么的</h2>
    <table>
      <thead><tr><th>后缀</th><th>用途</th><th>请求形态</th><th>说明</th></tr></thead>
      <tbody>
        {% for item in endpoint_guide %}
        <tr>
          <td><code>{{ item.path }}</code></td>
          <td>{{ item.purpose }}</td>
          <td>{{ item.request_shape }}</td>
          <td>{{ item.notes }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    <h2>7. 高级预设里包含什么</h2>
    <ul>
      {% for preset in endpoint_presets %}
      <li><strong>{{ preset.label }}</strong>：{{ preset.description }}</li>
      {% endfor %}
    </ul>
    <h2>8. 真正调用 API 时这些后缀怎么用</h2>
    <p>客户端通常不会自动轮流试所有后缀，而是按自己的实现直接打一个固定接口。例如老客户端常打 <code>/chat/completions</code>，新 SDK 更可能打 <code>/responses</code>，做 RAG 的打 <code>/embeddings</code>，做图片的打 <code>/images/generations</code>。探测工具的意义就在于提前把哪个接口能用、哪个不能用测清楚。</p>
    <h2>9. 页面上的结果怎么看</h2>
    <p>摘要里会给出 PASS/FAIL、总耗时、平均耗时、最慢项。Models 成功时，还会给出文本、视觉、向量、图片的候选优先级。每张卡片默认只展示摘要，点查看详情才展开完整 JSON。</p>
    <h2>10. 示例</h2>
    <pre>Base URL: https://example.com/v1
Endpoint Paths:
/v1/images/edits
/v1/audio/transcriptions

如果只想测你手填的特殊接口：
Endpoint Strategy = Custom Only

如果想测得更全：
Probe Mode = Deep
再勾上 Capabilities 或 Docs</pre>
  </main>
</body>
</html>
"""


@app.route("/")
def index() -> str:
    return render_template_string(
        PAGE_TEMPLATE,
        form=_default_form(),
        probe_options=PROBE_OPTIONS,
        endpoint_presets=ENDPOINT_PRESET_GROUPS,
        endpoint_presets_json=json.dumps(ENDPOINT_PRESET_GROUPS, ensure_ascii=False),
    )


@app.route("/docs-page")
def docs_page() -> str:
    return render_template_string(
        DOCS_TEMPLATE,
        endpoint_presets=ENDPOINT_PRESET_GROUPS,
        endpoint_guide=ENDPOINT_GUIDE,
    )


@app.route("/api/probe/notice", methods=["POST"])
def api_probe_notice():
    payload = request.get_json(force=True)
    notice = build_notice(
        payload.get("base_url", ""),
        payload.get("endpoint_paths", ""),
        payload.get("endpoint_strategy", "append"),
    )
    return jsonify({"notice": notice})


@app.route("/api/probe/start", methods=["POST"])
def api_probe_start():
    payload = request.get_json(force=True)
    base_url = str(payload.get("base_url", "")).strip()
    api_key = str(payload.get("api_key", "")).strip()
    if not base_url or not api_key:
        return jsonify({"error": "Base URL 和 API Key 都是必填。"}), 400

    endpoint_paths = _parse_textarea_list(str(payload.get("endpoint_paths", "")))
    preset_groups = payload.get("endpoint_preset_groups") or []
    extra_preset_paths = _preset_paths_from_values([str(item) for item in preset_groups])
    merged_endpoint_paths = endpoint_paths + extra_preset_paths
    enabled_probes = [str(item) for item in (payload.get("enabled_probes") or DEFAULT_UI_PROBES[:])]
    if merged_endpoint_paths and "extra_endpoints" not in enabled_probes:
        enabled_probes.append("extra_endpoints")

    timeout = int(payload.get("timeout") or DEFAULT_TIMEOUT)
    probe_mode = str(payload.get("probe_mode") or "quick")
    endpoint_strategy = str(payload.get("endpoint_strategy") or "append")
    estimated_seconds = _estimate_seconds(timeout, len(enabled_probes), probe_mode, len(merged_endpoint_paths))

    job_id = uuid.uuid4().hex
    with PROBE_JOBS_LOCK:
        PROBE_JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "progress": 0,
            "current_stage": "queued",
            "current_message": "任务已创建，准备开始",
            "current_meta": {},
            "results": [],
            "summary": {},
            "error": "",
            "estimated_seconds": estimated_seconds,
            "cancel_requested": False,
            "created_at": time.time(),
            "updated_at": time.time(),
        }

    worker_payload = {
        "base_url": base_url,
        "api_key": api_key,
        "timeout": timeout,
        "probe_mode": probe_mode,
        "endpoint_strategy": endpoint_strategy,
        "endpoint_paths": merged_endpoint_paths,
        "text_models": _parse_textarea_list(str(payload.get("text_models", ""))),
        "vision_models": _parse_textarea_list(str(payload.get("vision_models", ""))),
        "enabled_probes": enabled_probes,
    }

    thread = threading.Thread(target=_run_probe_job, args=(job_id, worker_payload), daemon=True)
    thread.start()
    return jsonify({"job_id": job_id, "estimated_seconds": estimated_seconds})


@app.route("/api/probe/cancel/<job_id>", methods=["POST"])
def api_probe_cancel(job_id: str):
    with PROBE_JOBS_LOCK:
        job = PROBE_JOBS.get(job_id)
        if not job:
            return jsonify({"error": "job not found"}), 404
        job["cancel_requested"] = True
        job["updated_at"] = time.time()
    return jsonify({"message": "已发送终止请求，当前请求结束后会尽快停止。"})


@app.route("/api/probe/status/<job_id>")
def api_probe_status(job_id: str):
    with PROBE_JOBS_LOCK:
        job = PROBE_JOBS.get(job_id)
        if not job:
            return jsonify({"error": "job not found"}), 404
        return jsonify(_safe_job_payload(job))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False, threaded=True)
