import argparse
import json
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests


DEFAULT_TIMEOUT = 20


@dataclass
class ProbeResult:
    name: str
    ok: bool
    status_code: Optional[int]
    summary: str
    details: Dict[str, Any]
    elapsed_ms: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "status_code": self.status_code,
            "summary": self.summary,
            "details": self.details,
            "elapsed_ms": self.elapsed_ms,
        }


class GatewayProber:
    def __init__(self, base_url: str, api_key: str, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "gateway-prober/0.1",
            }
        )
        self.models: List[Dict[str, Any]] = []

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = f"{self.base_url}{path}"
        return self.session.request(method=method, url=url, timeout=self.timeout, **kwargs)

    def _run_probe(self, name: str, fn) -> ProbeResult:
        started = time.time()
        try:
            payload = fn()
            elapsed_ms = int((time.time() - started) * 1000)
            return ProbeResult(
                name=name,
                ok=payload["ok"],
                status_code=payload.get("status_code"),
                summary=payload["summary"],
                details=payload.get("details", {}),
                elapsed_ms=elapsed_ms,
            )
        except requests.Timeout:
            elapsed_ms = int((time.time() - started) * 1000)
            return ProbeResult(
                name=name,
                ok=False,
                status_code=None,
                summary="request timed out",
                details={},
                elapsed_ms=elapsed_ms,
            )
        except Exception as exc:  # pragma: no cover
            elapsed_ms = int((time.time() - started) * 1000)
            return ProbeResult(
                name=name,
                ok=False,
                status_code=None,
                summary=str(exc),
                details={},
                elapsed_ms=elapsed_ms,
            )

    def _best_chat_model(self) -> Optional[str]:
        preferred = [
            "gpt-5.4",
            "gpt-5.3-codex",
            "gpt-5.2-codex",
            "gpt-5.1-codex",
            "gpt-5-codex",
            "gpt-5",
        ]
        ids = [item.get("id", "") for item in self.models]
        for model_id in preferred:
            if model_id in ids:
                return model_id
        for model_id in ids:
            lower = model_id.lower()
            if "image" in lower or "imagine" in lower or "embedding" in lower:
                continue
            return model_id
        return None

    def _best_image_model(self) -> Optional[str]:
        for item in self.models:
            model_id = item.get("id", "")
            lower = model_id.lower()
            if "image" in lower or "imagine" in lower:
                return model_id
        return None

    def probe_models(self) -> Dict[str, Any]:
        response = self._request("GET", "/v1/models")
        details: Dict[str, Any] = {"status_code": response.status_code}
        if not response.ok:
            details["body"] = response.text[:500]
            return {
                "ok": False,
                "status_code": response.status_code,
                "summary": "failed to list models",
                "details": details,
            }
        data = response.json()
        self.models = data.get("data", [])
        details["model_count"] = len(self.models)
        details["model_ids"] = [item.get("id") for item in self.models]
        return {
            "ok": True,
            "status_code": response.status_code,
            "summary": f"listed {len(self.models)} model(s)",
            "details": details,
        }

    def probe_chat(self) -> Dict[str, Any]:
        model = self._best_chat_model()
        if not model:
            return {"ok": False, "status_code": None, "summary": "no text model found", "details": {}}
        body = {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with exactly: OK_CHAT"}],
            "temperature": 0,
            "max_tokens": 20,
        }
        response = self._request("POST", "/v1/chat/completions", data=json.dumps(body))
        details: Dict[str, Any] = {"model": model, "status_code": response.status_code}
        if not response.ok:
            details["body"] = response.text[:500]
            return {
                "ok": False,
                "status_code": response.status_code,
                "summary": "chat/completions failed",
                "details": details,
            }
        payload = response.json()
        message = payload["choices"][0]["message"]
        content = message.get("content")
        details["content"] = content
        details["finish_reason"] = payload["choices"][0].get("finish_reason")
        return {
            "ok": content == "OK_CHAT",
            "status_code": response.status_code,
            "summary": "chat/completions works" if content == "OK_CHAT" else "chat/completions returned unexpected output",
            "details": details,
        }

    def probe_tools(self) -> Dict[str, Any]:
        model = self._best_chat_model()
        if not model:
            return {"ok": False, "status_code": None, "summary": "no text model found", "details": {}}
        body = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": 'Use the tool named get_status with argument {"asset":"equity"}.',
                }
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_status",
                        "description": "Return a market status",
                        "parameters": {
                            "type": "object",
                            "properties": {"asset": {"type": "string"}},
                            "required": ["asset"],
                        },
                    },
                }
            ],
            "tool_choice": "auto",
            "temperature": 0,
            "max_tokens": 80,
        }
        response = self._request("POST", "/v1/chat/completions", data=json.dumps(body))
        details: Dict[str, Any] = {"model": model, "status_code": response.status_code}
        if not response.ok:
            details["body"] = response.text[:500]
            return {
                "ok": False,
                "status_code": response.status_code,
                "summary": "tool calling failed",
                "details": details,
            }
        payload = response.json()
        tool_calls = payload["choices"][0]["message"].get("tool_calls") or []
        details["tool_calls"] = tool_calls
        ok = bool(tool_calls) and tool_calls[0].get("function", {}).get("name") == "get_status"
        return {
            "ok": ok,
            "status_code": response.status_code,
            "summary": "tool calling works" if ok else "tool calling unavailable or malformed",
            "details": details,
        }

    def probe_responses(self) -> Dict[str, Any]:
        model = self._best_chat_model()
        if not model:
            return {"ok": False, "status_code": None, "summary": "no text model found", "details": {}}
        body = {
            "model": model,
            "input": "Reply with exactly: OK_RESPONSES",
            "max_output_tokens": 20,
        }
        response = self._request("POST", "/v1/responses", data=json.dumps(body))
        details: Dict[str, Any] = {"model": model, "status_code": response.status_code}
        if not response.ok:
            details["body"] = response.text[:500]
            return {
                "ok": False,
                "status_code": response.status_code,
                "summary": "responses API failed",
                "details": details,
            }
        payload = response.json()
        details["response_keys"] = list(payload.keys())
        output_text = ""
        for item in payload.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"}:
                    output_text += content.get("text", "")
        details["output_text"] = output_text
        ok = "OK_RESPONSES" in output_text
        return {
            "ok": ok,
            "status_code": response.status_code,
            "summary": "responses API works" if ok else "responses API returned unexpected payload",
            "details": details,
        }

    def probe_embeddings(self) -> Dict[str, Any]:
        body = {"model": self._best_chat_model() or "text-embedding-3-small", "input": "macro rotation"}
        response = self._request("POST", "/v1/embeddings", data=json.dumps(body))
        details: Dict[str, Any] = {"status_code": response.status_code}
        if not response.ok:
            details["body"] = response.text[:500]
            return {
                "ok": False,
                "status_code": response.status_code,
                "summary": "embeddings failed",
                "details": details,
            }
        payload = response.json()
        data = payload.get("data") or []
        vector = data[0].get("embedding") if data else None
        details["vector_length"] = len(vector) if isinstance(vector, list) else None
        return {
            "ok": isinstance(vector, list) and len(vector) > 0,
            "status_code": response.status_code,
            "summary": "embeddings work" if isinstance(vector, list) and len(vector) > 0 else "embeddings payload malformed",
            "details": details,
        }

    def probe_images(self) -> Dict[str, Any]:
        model = self._best_image_model()
        if not model:
            return {
                "ok": False,
                "status_code": None,
                "summary": "no image model found",
                "details": {},
            }
        body = {"model": model, "prompt": "A red square on white background", "size": "1024x1024"}
        response = self._request("POST", "/v1/images/generations", data=json.dumps(body))
        details: Dict[str, Any] = {"model": model, "status_code": response.status_code}
        if not response.ok:
            details["body"] = response.text[:500]
            return {
                "ok": False,
                "status_code": response.status_code,
                "summary": "image generation failed",
                "details": details,
            }
        payload = response.json()
        data = payload.get("data") or []
        first = data[0] if data else {}
        ok = bool(first.get("b64_json") or first.get("url"))
        details["image_fields"] = sorted(first.keys())
        return {
            "ok": ok,
            "status_code": response.status_code,
            "summary": "image generation works" if ok else "image payload malformed",
            "details": details,
        }

    def probe_docs(self) -> Dict[str, Any]:
        docs: List[Dict[str, Any]] = []
        for path in ["/docs", "/openapi.json", "/health", "/version"]:
            try:
                response = self._request("GET", path)
                docs.append(
                    {
                        "path": path,
                        "status_code": response.status_code,
                        "content_type": response.headers.get("Content-Type", ""),
                    }
                )
            except requests.Timeout:
                docs.append({"path": path, "status_code": None, "content_type": "timeout"})
        return {
            "ok": True,
            "status_code": None,
            "summary": "collected documentation endpoint metadata",
            "details": {"endpoints": docs},
        }

    def run(self) -> List[ProbeResult]:
        probes = [
            ("models", self.probe_models),
            ("chat_completions", self.probe_chat),
            ("tool_calling", self.probe_tools),
            ("responses", self.probe_responses),
            ("embeddings", self.probe_embeddings),
            ("images", self.probe_images),
            ("docs", self.probe_docs),
        ]
        return [self._run_probe(name, fn) for name, fn in probes]


def print_text_report(results: List[ProbeResult]) -> None:
    print("Gateway Capability Report")
    print("=" * 80)
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        code = result.status_code if result.status_code is not None else "-"
        print(f"[{status}] {result.name} (status={code}, {result.elapsed_ms}ms)")
        print(f"  {result.summary}")
        if result.details:
            compact = json.dumps(result.details, ensure_ascii=False, indent=2)
            for line in compact.splitlines():
                print(f"  {line}")
    print("=" * 80)


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe an OpenAI-compatible gateway.")
    parser.add_argument("--base-url", required=True, help="Gateway base URL, for example https://example.com")
    parser.add_argument("--api-key", required=True, help="API key for the gateway")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Per-request timeout in seconds")
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format",
    )
    args = parser.parse_args()

    prober = GatewayProber(base_url=args.base_url, api_key=args.api_key, timeout=args.timeout)
    results = prober.run()

    if args.format == "json":
        payload = [item.to_dict() for item in results]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_text_report(results)

    return 0 if any(item.ok for item in results) else 1


if __name__ == "__main__":
    sys.exit(main())
