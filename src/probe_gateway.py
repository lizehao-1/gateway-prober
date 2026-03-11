import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

import requests


DEFAULT_TIMEOUT = 20
DEFAULT_ENDPOINT_CANDIDATES = {
    "chat": ["/v1/chat/completions"],
    "responses": ["/v1/responses", "/v1/responses/compact"],
    "embeddings": ["/v1/embeddings"],
    "images": ["/v1/images/generations"],
}
QUICK_API_ROOT_CANDIDATES = ["/v1", ""]
DEEP_API_ROOT_CANDIDATES = ["/v1", "", "/openai/v1", "/api/v1", "/api/openai/v1"]
DEFAULT_PROBES = [
    "models",
    "chat_completions",
    "tool_calling",
    "responses",
    "embeddings",
    "images",
]
DEFAULT_TEXT_MODELS = [
    "gpt-4.1",
    "gpt-4o",
    "claude-sonnet-4-5",
    "deepseek-chat",
]
DEFAULT_VISION_MODELS = [
    "gpt-4o",
    "gpt-4.1",
    "claude-sonnet-4-5",
]
DEFAULT_EMBEDDING_MODELS = [
    "text-embedding-3-small",
    "text-embedding-3-large",
    "text-embedding-ada-002",
]


class ProbeCancelledError(Exception):
    pass


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


def _normalize_base_url(base_url: str) -> str:
    text = (base_url or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = f"http://{text.lstrip('/')}"
    return text.rstrip("/")


def _normalize_list(value: Optional[str]) -> List[str]:
    if value is None:
        return []
    text = value.strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except Exception:
        pass
    items = text.replace("\n", ",").split(",")
    return [item.strip() for item in items if item.strip()]


def _dedupe_strings(items: List[str]) -> List[str]:
    seen = set()
    output: List[str] = []
    for item in items:
        lowered = item.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        output.append(item)
    return output


def _matches_any_suffix(path: str, suffixes: List[str]) -> Optional[str]:
    for suffix in suffixes:
        if path.endswith(suffix):
            return suffix
    return None


def _sanitize_attempt_result(result: Dict[str, Any]) -> Dict[str, Any]:
    sanitized: Dict[str, Any] = {
        "ok": bool(result.get("ok")),
        "status_code": result.get("status_code"),
        "details": result.get("details", {}),
    }
    if "stop_retry" in result:
        sanitized["stop_retry"] = bool(result.get("stop_retry"))
    return sanitized


class GatewayProber:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: int = DEFAULT_TIMEOUT,
        endpoint_paths: Optional[List[str]] = None,
        text_models: Optional[List[str]] = None,
        vision_models: Optional[List[str]] = None,
        probe_mode: str = "quick",
        enabled_probes: Optional[List[str]] = None,
        endpoint_strategy: str = "append",
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        cancel_callback: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.base_url = _normalize_base_url(base_url)
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "gateway-prober/0.2",
            }
        )
        self.models: List[Dict[str, Any]] = []
        self.endpoint_hints = endpoint_paths or []
        self.configured_text_models = text_models or []
        self.configured_vision_models = vision_models or []
        self.probe_mode = probe_mode if probe_mode in {"quick", "deep"} else "quick"
        self.enabled_probes = enabled_probes or DEFAULT_PROBES[:]
        self.endpoint_strategy = endpoint_strategy if endpoint_strategy in {"append", "custom_only"} else "append"
        self.progress_callback = progress_callback
        self.cancel_callback = cancel_callback
        self.api_roots = self._resolve_api_roots()
        self.endpoint_candidates = self._resolve_endpoint_candidates()
        self.chat_endpoint = self._pick_endpoint("chat")
        self.responses_endpoint = self._pick_endpoint("responses")
        self.embeddings_endpoint = self._pick_endpoint("embeddings")
        self.images_endpoint = self._pick_endpoint("images")

    def _request_proxies(self) -> Optional[Dict[str, Optional[str]]]:
        host = (urlparse(self.base_url).hostname or "").lower()
        if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
            return {"http": None, "https": None}
        return None

    def _check_cancel(self) -> None:
        if self.cancel_callback and self.cancel_callback():
            raise ProbeCancelledError("probe cancelled")

    def _request_timeout_for(self, probe_name: str) -> int:
        if probe_name == "embeddings":
            return max(3, min(self.timeout, 6))
        if probe_name == "images":
            return max(5, min(self.timeout, 10))
        if probe_name in {"docs", "extra_endpoints"}:
            return max(2, min(self.timeout, 4))
        return self.timeout

    def _request(self, method: str, url: str, timeout: Optional[int] = None, **kwargs: Any) -> requests.Response:
        self._check_cancel()
        return self.session.request(
            method=method,
            url=url,
            timeout=timeout if timeout is not None else self.timeout,
            proxies=self._request_proxies(),
            **kwargs,
        )

    def _report_progress(self, *, stage: str, message: str, progress: Optional[int] = None, meta: Optional[Dict[str, Any]] = None) -> None:
        if not self.progress_callback:
            return
        payload: Dict[str, Any] = {"stage": stage, "message": message}
        if progress is not None:
            payload["progress"] = progress
        if meta:
            payload["meta"] = meta
        self.progress_callback(payload)

    def _join_endpoint(self, base_path: str, endpoint_path: str) -> str:
        base_path = (base_path or "").rstrip("/")
        endpoint_path = (endpoint_path or "").strip()
        if not endpoint_path:
            return base_path or "/"
        if not endpoint_path.startswith("/"):
            endpoint_path = f"/{endpoint_path}"
        if not base_path:
            return endpoint_path
        if base_path.endswith("/v1") and endpoint_path.startswith("/v1/"):
            return f"{base_path}{endpoint_path[3:]}"
        return f"{base_path}{endpoint_path}"

    def _resolve_api_roots(self) -> List[str]:
        parsed = urlparse(self.base_url)
        path = (parsed.path or "").rstrip("/")
        all_suffixes: List[str] = []
        for values in DEFAULT_ENDPOINT_CANDIDATES.values():
            all_suffixes.extend(values)
        matched_suffix = _matches_any_suffix(path, all_suffixes)
        if matched_suffix:
            root_path = path[: -len(matched_suffix)] or ""
            seed = f"{parsed.scheme}://{parsed.netloc}{root_path}".rstrip("/")
            candidates = [seed]
            if not seed.endswith("/v1"):
                candidates.append(f"{seed}/v1".rstrip("/"))
            return _dedupe_strings(candidates)
        candidate_paths = QUICK_API_ROOT_CANDIDATES if self.probe_mode == "quick" else DEEP_API_ROOT_CANDIDATES
        base_root = f"{parsed.scheme}://{parsed.netloc}"
        roots: List[str] = []
        if path:
            roots.append(f"{base_root}{path}")
        for candidate_path in candidate_paths:
            roots.append(f"{base_root}{candidate_path}".rstrip("/"))
        return _dedupe_strings([root.rstrip("/") for root in roots if root.rstrip("/")])

    def _resolve_endpoint_candidates(self) -> List[Dict[str, str]]:
        candidates: List[Dict[str, str]] = []

        def add_candidate(url: str, label: str, kind: str) -> None:
            normalized = (url or "").rstrip("/")
            if not normalized:
                return
            if any(item["url"] == normalized for item in candidates):
                return
            candidates.append({"url": normalized, "label": label, "kind": kind})

        if self.endpoint_strategy != "custom_only" or not self.endpoint_hints:
            for root in self.api_roots:
                root_path = (urlparse(root).path or "").rstrip("/")
                for kind, suffixes in DEFAULT_ENDPOINT_CANDIDATES.items():
                    for suffix in suffixes:
                        full_path = self._join_endpoint(root_path, suffix)
                        add_candidate(f"{urlparse(root).scheme}://{urlparse(root).netloc}{full_path}", full_path, kind)

        for hint in self.endpoint_hints:
            if hint.startswith("http://") or hint.startswith("https://"):
                hint_path = (urlparse(hint).path or "").rstrip("/") or hint
                hint_kind = self._kind_for_path(hint_path)
                add_candidate(hint, hint_path, hint_kind)
                continue
            for root in self.api_roots:
                root_path = (urlparse(root).path or "").rstrip("/")
                base_root = f"{urlparse(root).scheme}://{urlparse(root).netloc}"
                full_path = self._join_endpoint(root_path, hint)
                hint_kind = self._kind_for_path(full_path)
                add_candidate(f"{base_root}{full_path}", full_path, hint_kind)

        return candidates

    def _kind_for_path(self, path: str) -> str:
        lowered = path.lower()
        if lowered.endswith("/chat/completions"):
            return "chat"
        if lowered.endswith("/responses") or lowered.endswith("/responses/compact"):
            return "responses"
        if lowered.endswith("/embeddings"):
            return "embeddings"
        if lowered.endswith("/images/generations"):
            return "images"
        return "responses"

    def _pick_endpoint(self, kind: str) -> Optional[Dict[str, str]]:
        for candidate in self.endpoint_candidates:
            if candidate["kind"] == kind:
                return candidate
        return None

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
        except ProbeCancelledError:
            elapsed_ms = int((time.time() - started) * 1000)
            return ProbeResult(
                name=name,
                ok=False,
                status_code=None,
                summary="probe cancelled",
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
            if any(word in lower for word in ["image", "imagine", "embedding", "tts", "whisper"]):
                continue
            return model_id
        return None

    def _best_image_model(self) -> Optional[str]:
        for item in self.models:
            model_id = item.get("id", "")
            lower = model_id.lower()
            if any(word in lower for word in ["image", "imagine", "vision", "vl"]):
                return model_id
        return None

    def _extract_version_score(self, model_id: str) -> float:
        matches = re.findall(r"(\d+(?:\.\d+)*)", model_id.lower())
        if not matches:
            return 0.0
        try:
            parts = [float(item) for item in matches[0].split(".")]
            score = 0.0
            weight = 1.0
            for part in parts:
                score += part * weight
                weight /= 10
            return score
        except Exception:
            return 0.0

    def _text_model_score(self, model_id: str) -> float:
        lower = model_id.lower()
        score = self._extract_version_score(lower)
        if "gpt-5" in lower:
            score += 60
        elif "gpt-4.1" in lower:
            score += 56
        elif "gpt-4o" in lower:
            score += 54
        elif "claude" in lower:
            score += 50
        elif "gemini" in lower:
            score += 46
        elif "deepseek" in lower:
            score += 42
        if any(word in lower for word in ["opus", "ultra", "max", "pro"]):
            score += 8
        if any(word in lower for word in ["sonnet", "reasoner", "r1"]):
            score += 6
        if any(word in lower for word in ["mini", "nano", "lite", "flash", "haiku"]):
            score -= 6
        if any(word in lower for word in ["image", "imagine", "embedding", "tts", "whisper", "audio"]):
            score -= 30
        return score

    def _vision_model_score(self, model_id: str) -> float:
        lower = model_id.lower()
        score = self._extract_version_score(lower)
        if "gpt-4o" in lower:
            score += 60
        elif "gpt-4.1" in lower:
            score += 57
        elif "claude" in lower:
            score += 53
        elif "gemini" in lower:
            score += 50
        if any(word in lower for word in ["vision", "vl", "image", "omni", "multimodal"]):
            score += 12
        if any(word in lower for word in ["pro", "max"]):
            score += 6
        if any(word in lower for word in ["flash", "lite", "mini", "haiku"]):
            score -= 4
        return score

    def _embedding_model_score(self, model_id: str) -> float:
        lower = model_id.lower()
        score = self._extract_version_score(lower)
        if "embedding" in lower:
            score += 50
        if "large" in lower:
            score += 6
        if "small" in lower:
            score += 2
        return score

    def _sort_models_by_score(self, models: List[str], scorer: Callable[[str], float]) -> List[str]:
        deduped = _dedupe_strings(models)
        return sorted(deduped, key=lambda item: (scorer(item), item.lower()), reverse=True)

    def _looks_like_vision_model(self, model_id: str) -> bool:
        lower = model_id.lower()
        keywords = ["vision", "vl", "image", "gpt-4o", "gpt-4.1", "claude", "gemini"]
        return any(keyword in lower for keyword in keywords)

    def _pick_text_probe_models(self) -> List[str]:
        configured = _dedupe_strings(self.configured_text_models)
        if configured:
            return self._sort_models_by_score(configured, self._text_model_score)[:6]
        models_from_api = [item.get("id", "") for item in self.models if item.get("id")]
        filtered = []
        for model_id in models_from_api:
            lower = model_id.lower()
            if any(word in lower for word in ["image", "imagine", "embedding", "tts", "whisper"]):
                continue
            filtered.append(model_id)
        candidates = self._sort_models_by_score(filtered + DEFAULT_TEXT_MODELS, self._text_model_score)
        return candidates[:6]

    def _pick_vision_probe_models(self) -> List[str]:
        configured = _dedupe_strings(self.configured_vision_models)
        if configured:
            return self._sort_models_by_score(configured, self._vision_model_score)[:6]
        models_from_api = [item.get("id", "") for item in self.models if item.get("id")]
        filtered = [model_id for model_id in models_from_api if self._looks_like_vision_model(model_id)]
        if filtered:
            return self._sort_models_by_score(filtered + DEFAULT_VISION_MODELS, self._vision_model_score)[:6]
        image_model = self._best_image_model()
        if image_model:
            return [image_model]
        return self._sort_models_by_score(DEFAULT_VISION_MODELS[:], self._vision_model_score)[:6]

    def _pick_image_generation_models(self) -> List[str]:
        configured = _dedupe_strings(self.configured_vision_models)
        models_from_api = [item.get("id", "") for item in self.models if item.get("id")]
        image_models = [
            model_id for model_id in models_from_api
            if any(keyword in model_id.lower() for keyword in ["image", "imagine", "gpt-image", "dall-e", "flux", "sd"])
        ]
        best = self._best_image_model()
        candidates = self._sort_models_by_score(([best] if best else []) + image_models + configured, self._vision_model_score)
        return candidates[:6]

    def _pick_embedding_probe_models(self) -> List[str]:
        configured = _dedupe_strings(self.configured_text_models)
        models_from_api = [item.get("id", "") for item in self.models if item.get("id")]
        embedding_models = [model_id for model_id in models_from_api if "embedding" in model_id.lower()]
        candidates = self._sort_models_by_score(configured + embedding_models + DEFAULT_EMBEDDING_MODELS, self._embedding_model_score)
        return candidates[:6]

    def _try_model_candidates(self, models: List[str], runner, probe_name: str):
        attempts: List[Dict[str, Any]] = []
        for model in _dedupe_strings(models):
            self._check_cancel()
            self._report_progress(stage=probe_name, message=f"{probe_name}: trying model {model}", meta={"model": model})
            try:
                result = runner(model)
            except requests.Timeout:
                result = {
                    "ok": False,
                    "status_code": None,
                    "details": {
                        "model": model,
                        "timeout": True,
                    },
                    "stop_retry": False,
                }
            except requests.RequestException as exc:
                result = {
                    "ok": False,
                    "status_code": None,
                    "details": {
                        "model": model,
                        "error": str(exc),
                    },
                    "stop_retry": False,
                }
            sanitized = _sanitize_attempt_result(result)
            attempts.append(sanitized)
            if sanitized.get("ok"):
                return {
                    "ok": True,
                    "status_code": sanitized.get("status_code"),
                    "details": sanitized.get("details", {}),
                    "attempts": attempts,
                }
            if sanitized.get("stop_retry"):
                break
        return {
            "ok": False,
            "status_code": attempts[-1].get("status_code") if attempts else None,
            "attempts": attempts,
            "last": attempts[-1] if attempts else {},
        }

    def _should_stop_retry(self, status_code: Optional[int], body: str = "") -> bool:
        if status_code in {401, 403, 404, 405, 415, 429, 501}:
            return True
        lowered = (body or "").lower()
        stop_signals = [
            "unknown url",
            "not found",
            "method not allowed",
            "unsupported media type",
            "invalid api key",
            "authentication",
            "authorization",
        ]
        return any(signal in lowered for signal in stop_signals)

    def probe_models(self) -> Dict[str, Any]:
        attempts: List[Dict[str, Any]] = []
        for root in self.api_roots:
            models_url = f"{root}/models"
            response = self._request("GET", models_url)
            attempt = {"url": models_url, "status_code": response.status_code}
            if response.ok:
                data = response.json()
                self.models = data.get("data", [])
                attempt["model_count"] = len(self.models)
                attempts.append(attempt)
                return {
                    "ok": True,
                    "status_code": response.status_code,
                    "summary": f"listed {len(self.models)} model(s)",
                    "details": {
                        "url": models_url,
                        "api_roots": self.api_roots,
                        "attempts": attempts,
                        "model_count": len(self.models),
                        "model_ids": [item.get("id") for item in self.models],
                        "rankings": {
                            "text": self._pick_text_probe_models(),
                            "vision": self._pick_vision_probe_models(),
                            "embeddings": self._pick_embedding_probe_models(),
                            "images": self._pick_image_generation_models(),
                        },
                    },
                }
            attempt["body"] = response.text[:200]
            attempts.append(attempt)
        return {
            "ok": False,
            "status_code": attempts[-1]["status_code"] if attempts else None,
            "summary": "failed to list models",
            "details": {"api_roots": self.api_roots, "attempts": attempts},
        }

    def probe_chat(self) -> Dict[str, Any]:
        endpoint = self.chat_endpoint
        if not endpoint:
            return {"ok": False, "status_code": None, "summary": "chat endpoint not configured", "details": {}}
        candidate_models = self._pick_text_probe_models()
        if not candidate_models:
            return {"ok": False, "status_code": None, "summary": "no text model found", "details": {}}

        def runner(model: str) -> Dict[str, Any]:
            body = {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with exactly: OK_CHAT"}],
                "temperature": 0,
                "max_tokens": 20,
            }
            response = self._request("POST", endpoint["url"], data=json.dumps(body), timeout=self._request_timeout_for("chat_completions"))
            details: Dict[str, Any] = {
                "model": model,
                "status_code": response.status_code,
                "endpoint": endpoint["label"],
                "url": endpoint["url"],
            }
            if not response.ok:
                details["body"] = response.text[:500]
                return {
                    "ok": False,
                    "status_code": response.status_code,
                    "details": details,
                    "stop_retry": self._should_stop_retry(response.status_code, details["body"]),
                }
            payload = response.json()
            message = payload["choices"][0]["message"]
            content = message.get("content")
            details["content"] = content
            details["finish_reason"] = payload["choices"][0].get("finish_reason")
            return {
                "ok": content == "OK_CHAT",
                "status_code": response.status_code,
                "details": details,
            }

        result = self._try_model_candidates(candidate_models, runner, "chat_completions")
        if not result["ok"]:
            return {
                "ok": False,
                "status_code": result["status_code"],
                "summary": "chat/completions failed",
                "details": {
                    "endpoint": endpoint["label"],
                    "url": endpoint["url"],
                    "candidate_models": candidate_models,
                    "attempts": result["attempts"],
                },
            }
        details = result["details"]
        details["attempts"] = result["attempts"]
        return {
            "ok": True,
            "status_code": result["status_code"],
            "summary": "chat/completions works",
            "details": details,
        }

    def probe_tools(self) -> Dict[str, Any]:
        endpoint = self.chat_endpoint
        if not endpoint:
            return {"ok": False, "status_code": None, "summary": "chat endpoint not configured", "details": {}}
        candidate_models = self._pick_text_probe_models()
        if not candidate_models:
            return {"ok": False, "status_code": None, "summary": "no text model found", "details": {}}

        def runner(model: str) -> Dict[str, Any]:
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
            response = self._request("POST", endpoint["url"], data=json.dumps(body), timeout=self._request_timeout_for("tool_calling"))
            details: Dict[str, Any] = {
                "model": model,
                "status_code": response.status_code,
                "endpoint": endpoint["label"],
                "url": endpoint["url"],
            }
            if not response.ok:
                details["body"] = response.text[:500]
                return {
                    "ok": False,
                    "status_code": response.status_code,
                    "details": details,
                    "stop_retry": self._should_stop_retry(response.status_code, details["body"]),
                }
            payload = response.json()
            tool_calls = payload["choices"][0]["message"].get("tool_calls") or []
            details["tool_calls"] = tool_calls
            ok = bool(tool_calls) and tool_calls[0].get("function", {}).get("name") == "get_status"
            return {"ok": ok, "status_code": response.status_code, "details": details}

        result = self._try_model_candidates(candidate_models, runner, "tool_calling")
        if not result["ok"]:
            return {
                "ok": False,
                "status_code": result["status_code"],
                "summary": "tool calling failed",
                "details": {
                    "endpoint": endpoint["label"],
                    "url": endpoint["url"],
                    "candidate_models": candidate_models,
                    "attempts": result["attempts"],
                },
            }
        details = result["details"]
        details["attempts"] = result["attempts"]
        return {
            "ok": True,
            "status_code": result["status_code"],
            "summary": "tool calling works",
            "details": details,
        }

    def probe_responses(self) -> Dict[str, Any]:
        endpoint = self.responses_endpoint
        if not endpoint:
            return {"ok": False, "status_code": None, "summary": "responses endpoint not configured", "details": {}}
        candidate_models = self._pick_text_probe_models()
        if not candidate_models:
            return {"ok": False, "status_code": None, "summary": "no text model found", "details": {}}

        def runner(model: str) -> Dict[str, Any]:
            body = {
                "model": model,
                "input": "Reply with exactly: OK_RESPONSES",
                "max_output_tokens": 20,
            }
            response = self._request("POST", endpoint["url"], data=json.dumps(body), timeout=self._request_timeout_for("responses"))
            details: Dict[str, Any] = {
                "model": model,
                "status_code": response.status_code,
                "endpoint": endpoint["label"],
                "url": endpoint["url"],
            }
            if not response.ok:
                details["body"] = response.text[:500]
                return {
                    "ok": False,
                    "status_code": response.status_code,
                    "details": details,
                    "stop_retry": self._should_stop_retry(response.status_code, details["body"]),
                }
            payload = response.json()
            details["response_keys"] = list(payload.keys())
            output_text = ""
            for item in payload.get("output", []):
                for content in item.get("content", []):
                    if content.get("type") in {"output_text", "text"}:
                        output_text += content.get("text", "")
            details["output_text"] = output_text
            return {
                "ok": "OK_RESPONSES" in output_text,
                "status_code": response.status_code,
                "details": details,
            }

        result = self._try_model_candidates(candidate_models, runner, "responses")
        if not result["ok"]:
            return {
                "ok": False,
                "status_code": result["status_code"],
                "summary": "responses API failed",
                "details": {
                    "endpoint": endpoint["label"],
                    "url": endpoint["url"],
                    "candidate_models": candidate_models,
                    "attempts": result["attempts"],
                },
            }
        details = result["details"]
        details["attempts"] = result["attempts"]
        return {
            "ok": True,
            "status_code": result["status_code"],
            "summary": "responses API works",
            "details": details,
        }

    def probe_embeddings(self) -> Dict[str, Any]:
        endpoint = self.embeddings_endpoint
        if not endpoint:
            return {"ok": False, "status_code": None, "summary": "embeddings endpoint not configured", "details": {}}
        candidate_models = self._pick_embedding_probe_models()

        def runner(model: str) -> Dict[str, Any]:
            body = {"model": model, "input": "macro rotation"}
            response = self._request("POST", endpoint["url"], data=json.dumps(body), timeout=self._request_timeout_for("embeddings"))
            details: Dict[str, Any] = {
                "model": model,
                "status_code": response.status_code,
                "url": endpoint["url"],
                "endpoint": endpoint["label"],
            }
            if not response.ok:
                details["body"] = response.text[:500]
                return {
                    "ok": False,
                    "status_code": response.status_code,
                    "details": details,
                    "stop_retry": self._should_stop_retry(response.status_code, details["body"]),
                }
            payload = response.json()
            data = payload.get("data") or []
            vector = data[0].get("embedding") if data else None
            details["vector_length"] = len(vector) if isinstance(vector, list) else None
            return {
                "ok": isinstance(vector, list) and len(vector) > 0,
                "status_code": response.status_code,
                "details": details,
            }

        result = self._try_model_candidates(candidate_models, runner, "embeddings")
        if not result["ok"]:
            return {
                "ok": False,
                "status_code": result["status_code"],
                "summary": "embeddings failed",
                "details": {
                    "endpoint": endpoint["label"],
                    "url": endpoint["url"],
                    "candidate_models": candidate_models,
                    "attempts": result["attempts"],
                },
            }
        details = result["details"]
        details["attempts"] = result["attempts"]
        return {
            "ok": True,
            "status_code": result["status_code"],
            "summary": "embeddings work",
            "details": details,
        }

    def probe_images(self) -> Dict[str, Any]:
        candidate_models = self._pick_image_generation_models()
        if not candidate_models:
            return {
                "ok": False,
                "status_code": None,
                "summary": "no image model found",
                "details": {},
            }
        endpoint = self.images_endpoint
        if not endpoint:
            return {"ok": False, "status_code": None, "summary": "image endpoint not configured", "details": {}}

        def runner(model: str) -> Dict[str, Any]:
            body = {"model": model, "prompt": "A red square on white background", "size": "1024x1024"}
            response = self._request("POST", endpoint["url"], data=json.dumps(body), timeout=self._request_timeout_for("images"))
            details: Dict[str, Any] = {
                "model": model,
                "status_code": response.status_code,
                "url": endpoint["url"],
                "endpoint": endpoint["label"],
            }
            if not response.ok:
                details["body"] = response.text[:500]
                return {
                    "ok": False,
                    "status_code": response.status_code,
                    "details": details,
                    "stop_retry": self._should_stop_retry(response.status_code, details["body"]),
                }
            payload = response.json()
            data = payload.get("data") or []
            first = data[0] if data else {}
            ok = bool(first.get("b64_json") or first.get("url"))
            details["image_fields"] = sorted(first.keys())
            return {"ok": ok, "status_code": response.status_code, "details": details}

        result = self._try_model_candidates(candidate_models, runner, "images")
        if not result["ok"]:
            return {
                "ok": False,
                "status_code": result["status_code"],
                "summary": "image generation failed",
                "details": {
                    "endpoint": endpoint["label"],
                    "url": endpoint["url"],
                    "candidate_models": candidate_models,
                    "attempts": result["attempts"],
                },
            }
        details = result["details"]
        details["attempts"] = result["attempts"]
        return {
            "ok": True,
            "status_code": result["status_code"],
            "summary": "image generation works",
            "details": details,
        }

    def probe_docs(self) -> Dict[str, Any]:
        docs: List[Dict[str, Any]] = []
        for path in ["/docs", "/openapi.json", "/health", "/version"]:
            try:
                response = self._request("GET", f"{self.base_url}{path}", timeout=self._request_timeout_for("docs"))
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

    def probe_extra_endpoints(self) -> Dict[str, Any]:
        if not self.endpoint_hints:
            return {
                "ok": True,
                "status_code": None,
                "summary": "no extra endpoints configured",
                "details": {"endpoints": []},
            }
        results: List[Dict[str, Any]] = []
        for index, hint in enumerate(_dedupe_strings(self.endpoint_hints), start=1):
            self._check_cancel()
            self._report_progress(
                stage="extra_endpoints",
                message=f"extra_endpoints: checking {hint} ({index}/{len(_dedupe_strings(self.endpoint_hints))})",
                meta={"path": hint},
            )
            if hint.startswith("http://") or hint.startswith("https://"):
                url = hint
            else:
                root = self.api_roots[0] if self.api_roots else self.base_url
                base_path = (urlparse(root).path or "").rstrip("/")
                full_path = self._join_endpoint(base_path, hint)
                url = f"{urlparse(root).scheme}://{urlparse(root).netloc}{full_path}"
            entry: Dict[str, Any] = {"path": hint, "url": url}
            try:
                response = self._request("OPTIONS", url, timeout=self._request_timeout_for("extra_endpoints"))
                entry["options_status"] = response.status_code
                if response.status_code >= 400:
                    response = self._request("GET", url, timeout=self._request_timeout_for("extra_endpoints"))
                    entry["get_status"] = response.status_code
                    entry["content_type"] = response.headers.get("Content-Type", "")
                else:
                    entry["allow"] = response.headers.get("Allow", "")
            except Exception as exc:
                entry["error"] = str(exc)
            results.append(entry)
        ok = any(
            any(item.get(key, 999) < 500 for key in ["options_status", "get_status"])
            for item in results
        )
        return {
            "ok": ok,
            "status_code": None,
            "summary": "extra endpoint probing finished",
            "details": {"endpoints": results},
        }

    def probe_capabilities(self) -> Dict[str, Any]:
        endpoint_results: Dict[str, Dict[str, Any]] = {}
        model_results: List[Dict[str, Any]] = []

        def ensure_endpoint(entry: Dict[str, str]) -> Dict[str, Any]:
            return endpoint_results.setdefault(
                entry["label"],
                {
                    "url": entry["url"],
                    "kind": entry["kind"],
                    "text_supported": False,
                    "vision_supported": False,
                    "supported": False,
                },
            )

        def probe_call(url: str, payload: Dict[str, Any]) -> requests.Response:
            return self._request("POST", url, data=json.dumps(payload))

        for model in self._pick_text_probe_models():
            self._check_cancel()
            endpoint_support: Dict[str, Dict[str, Any]] = {}
            text_supported = False
            last_status: Optional[int] = None
            last_body = ""
            for endpoint in self.endpoint_candidates:
                payload = (
                    {
                        "model": model,
                        "messages": [{"role": "user", "content": "Reply with exactly: OK_TEXT"}],
                        "max_tokens": 12,
                    }
                    if endpoint["kind"] == "chat"
                    else {
                        "model": model,
                        "input": "Reply with exactly: OK_TEXT",
                        "max_output_tokens": 12,
                    }
                )
                response = probe_call(endpoint["url"], payload)
                ok = response.ok
                body = response.text[:240]
                endpoint_support[endpoint["label"]] = {
                    "url": endpoint["url"],
                    "text_supported": ok,
                    "vision_supported": False,
                    "status_code": response.status_code,
                }
                info = ensure_endpoint(endpoint)
                if ok:
                    text_supported = True
                    info["text_supported"] = True
                    info["supported"] = True
                last_status = response.status_code
                last_body = body
            model_results.append(
                {
                    "name": model,
                    "kind": "text",
                    "ok": text_supported,
                    "status_code": last_status,
                    "summary": "text probing passed" if text_supported else "text probing failed",
                    "details": {
                        "capabilities": {"text": text_supported, "vision": None},
                        "endpoint_support": endpoint_support,
                        "last_error_body": last_body if not text_supported else "",
                    },
                }
            )

        for model in self._pick_vision_probe_models():
            self._check_cancel()
            endpoint_support: Dict[str, Dict[str, Any]] = {}
            vision_supported = False
            last_status: Optional[int] = None
            last_body = ""
            for endpoint in self.endpoint_candidates:
                payload = (
                    {
                        "model": model,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": "What color is this pixel? Reply with one word."},
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": "data:image/gif;base64,R0lGODlhAQABAIABAP///wAAACwAAAAAAQABAAACAkQBADs="
                                        },
                                    },
                                ],
                            }
                        ],
                        "max_tokens": 16,
                    }
                    if endpoint["kind"] == "chat"
                    else {
                        "model": model,
                        "input": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "input_text", "text": "What color is this pixel? Reply with one word."},
                                    {
                                        "type": "input_image",
                                        "image_url": "data:image/gif;base64,R0lGODlhAQABAIABAP///wAAACwAAAAAAQABAAACAkQBADs=",
                                    },
                                ],
                            }
                        ],
                        "max_output_tokens": 16,
                    }
                )
                response = probe_call(endpoint["url"], payload)
                ok = response.ok
                body = response.text[:240]
                endpoint_support[endpoint["label"]] = {
                    "url": endpoint["url"],
                    "text_supported": False,
                    "vision_supported": ok,
                    "status_code": response.status_code,
                }
                info = ensure_endpoint(endpoint)
                if ok:
                    vision_supported = True
                    info["vision_supported"] = True
                    info["supported"] = True
                last_status = response.status_code
                last_body = body
            model_results.append(
                {
                    "name": model,
                    "kind": "vision",
                    "ok": vision_supported,
                    "status_code": last_status,
                    "summary": "vision probing passed" if vision_supported else "vision probing failed",
                    "details": {
                        "capabilities": {"text": None, "vision": vision_supported},
                        "endpoint_support": endpoint_support,
                        "last_error_body": last_body if not vision_supported else "",
                    },
                }
            )

        details = {
            "base_url": self.base_url,
            "endpoint_candidates": self.endpoint_candidates,
            "endpoint_support": endpoint_results,
            "models": model_results,
        }
        ok = any(item.get("supported") for item in endpoint_results.values())
        return {
            "ok": ok,
            "status_code": None,
            "summary": "per-model capability probing finished",
            "details": details,
        }

    def run(self) -> List[ProbeResult]:
        probes = [
            ("models", self.probe_models),
            ("chat_completions", self.probe_chat),
            ("tool_calling", self.probe_tools),
            ("responses", self.probe_responses),
            ("embeddings", self.probe_embeddings),
            ("images", self.probe_images),
            ("extra_endpoints", self.probe_extra_endpoints),
            ("capabilities", self.probe_capabilities),
            ("docs", self.probe_docs),
        ]
        selected = [(name, fn) for name, fn in probes if name in self.enabled_probes]
        total = len(selected)
        results: List[ProbeResult] = []
        for index, (name, fn) in enumerate(selected, start=1):
            self._check_cancel()
            progress = int(((index - 1) / max(total, 1)) * 100)
            self._report_progress(stage=name, message=f"Starting {name} ({index}/{total})", progress=progress)
            result = self._run_probe(name, fn)
            results.append(result)
            if result.summary == "probe cancelled":
                self._report_progress(stage="cancelled", message="Probe cancelled", progress=progress)
                break
            progress = int((index / max(total, 1)) * 100)
            self._report_progress(
                stage=name,
                message=f"Finished {name}: {'PASS' if result.ok else 'FAIL'}",
                progress=progress,
                meta={"ok": result.ok, "elapsed_ms": result.elapsed_ms},
            )
        self._report_progress(stage="done", message="All probes completed", progress=100)
        return results


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


def _to_json_safe(value: Any, seen: Optional[set] = None) -> Any:
    if seen is None:
        seen = set()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        obj_id = id(value)
        if obj_id in seen:
            return "[circular]"
        seen.add(obj_id)
        return [_to_json_safe(item, seen) for item in value]
    if isinstance(value, dict):
        obj_id = id(value)
        if obj_id in seen:
            return "[circular]"
        seen.add(obj_id)
        return {str(key): _to_json_safe(item, seen) for key, item in value.items()}
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe an OpenAI-compatible gateway.")
    parser.add_argument("--base-url", required=True, help="Gateway base URL, for example https://example.com")
    parser.add_argument("--api-key", required=True, help="API key for the gateway")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Per-request timeout in seconds")
    parser.add_argument(
        "--endpoint-paths",
        default="",
        help="Comma-separated or JSON list of endpoint paths to probe",
    )
    parser.add_argument(
        "--text-models",
        default="",
        help="Comma-separated or JSON list of text models to test",
    )
    parser.add_argument(
        "--vision-models",
        default="",
        help="Comma-separated or JSON list of vision models to test",
    )
    parser.add_argument(
        "--probe-mode",
        choices=["quick", "deep"],
        default="quick",
        help="Endpoint discovery mode",
    )
    parser.add_argument(
        "--enable-probes",
        default="",
        help="Comma-separated or JSON list of probes to run",
    )
    parser.add_argument(
        "--endpoint-strategy",
        choices=["append", "custom_only"],
        default="append",
        help="Whether custom endpoint paths extend or replace default candidates",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format",
    )
    args = parser.parse_args()

    prober = GatewayProber(
        base_url=args.base_url,
        api_key=args.api_key,
        timeout=args.timeout,
        endpoint_paths=_normalize_list(args.endpoint_paths),
        text_models=_normalize_list(args.text_models),
        vision_models=_normalize_list(args.vision_models),
        probe_mode=args.probe_mode,
        enabled_probes=_normalize_list(args.enable_probes) or DEFAULT_PROBES[:],
        endpoint_strategy=args.endpoint_strategy,
    )
    results = prober.run()

    if args.format == "json":
        payload = [_to_json_safe(item.to_dict()) for item in results]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_text_report(results)

    return 0 if any(item.ok for item in results) else 1


if __name__ == "__main__":
    sys.exit(main())
