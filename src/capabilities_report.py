from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def _load_json(path: str) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _format_model_line(model: Dict[str, Any]) -> List[str]:
    details = model.get("details") or {}
    endpoint_support = details.get("endpoint_support") or {}
    ok_endpoints = []
    bad_endpoints = []
    for endpoint, info in endpoint_support.items():
        status = info.get("status_code", "-")
        if info.get("text_supported") or info.get("vision_supported"):
            ok_endpoints.append(f"{endpoint}({status})")
        else:
            bad_endpoints.append(f"{endpoint}({status})")
    lines = [f"- {model.get('name', '-')}: {'可用' if model.get('ok') else '不可用'}"]
    if ok_endpoints:
      lines.append(f"  可用端点：{'、'.join(ok_endpoints)}")
    if bad_endpoints:
      lines.append(f"  失败端点：{'、'.join(bad_endpoints)}")
    return lines


def build_report(payload: Dict[str, Any]) -> str:
    endpoint_support = payload.get("endpoint_support") or {}
    models = payload.get("models") or []
    supported = [key for key, value in endpoint_support.items() if value.get("supported")]
    unsupported = [key for key, value in endpoint_support.items() if not value.get("supported")]

    lines = ["Gateway 完整报告", ""]
    lines.append(f"Base URL: {payload.get('base_url', '-')}")
    lines.append("")
    lines.append("一、整体结论")
    if supported:
        lines.append(f"- 已测通端点：{'、'.join(supported)}")
    if unsupported:
        lines.append(f"- 未测通端点：{'、'.join(unsupported)}")
    lines.append("")
    lines.append("二、模型结论")
    for model in models[:10]:
        lines.extend(_format_model_line(model))
    lines.append("")
    lines.append("三、接入建议")
    if "/v1/chat/completions" in supported and "/v1/responses" in supported:
        lines.append("- 这个网关同时兼容 chat/completions 和 responses，接 IDE 或 SDK 都比较稳。")
    elif "/v1/chat/completions" in supported:
        lines.append("- 更推荐接传统 chat/completions 客户端。")
    elif "/v1/responses" in supported:
        lines.append("- 更推荐接新版 responses 风格客户端。")
    else:
        lines.append("- 文本主接口没有完全测通，不建议直接接生产 IDE 或 Agent。")
    if "/v1/embeddings" not in supported:
        lines.append("- Embeddings 未测通，不建议直接用于知识库问答、RAG、语义搜索。")
    if "/v1/images/generations" not in supported:
        lines.append("- 图片生成未测通，更适合文本场景。")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert capabilities JSON into a readable report.")
    parser.add_argument("json_path", help="Path to a capabilities JSON file.")
    args = parser.parse_args()
    payload = _load_json(args.json_path)
    print(build_report(payload))


if __name__ == "__main__":
    main()
