"""High-level local model calls; images are deliberately not accepted."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .metrics import RoundMetrics


class ModelError(RuntimeError):
    """Raised for a local model request or response failure."""


@dataclass(frozen=True)
class ModelReply:
    content: str
    input_tokens: int
    output_tokens: int


class LocalModelClient:
    """OpenAI-compatible client restricted to loopback endpoints by default."""

    def __init__(
        self,
        endpoint: str = "http://127.0.0.1:11434/v1",
        model: str = "huihui_ai/qwen3-abliterated:14b-v2-q4_K_M",
        timeout_seconds: int = 90,
        allow_remote: bool = False,
    ):
        uri = urllib.parse.urlparse(endpoint)
        if uri.scheme not in {"http", "https"} or not uri.hostname:
            raise ValueError("invalid model endpoint")
        if not allow_remote and uri.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("remote model endpoints require allow_remote")
        path = uri.path.rstrip("/")
        if not path.endswith("/chat/completions"):
            path += "/chat/completions"
        self.endpoint = urllib.parse.urlunparse(
            (uri.scheme, uri.netloc, path, "", "", "")
        )
        self.model = model
        self.timeout_seconds = timeout_seconds

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        metrics: RoundMetrics | None = None,
        max_tokens: int = 700,
    ) -> ModelReply:
        """Return model text and exact API-reported token usage."""

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt + "\n/no_think"},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "stream": False,
            "think": False,
            "keep_alive": "15s",
            "options": {
                "num_predict": max_tokens,
                "num_ctx": 2048,
                "num_batch": 64,
            },
        }
        if metrics is not None:
            metrics.record_tool_call()
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError) as exception:
            raise ModelError(str(exception)) from exception

        try:
            parsed: dict[str, Any] = json.loads(body)
            content = parsed["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exception:
            raise ModelError("invalid OpenAI-compatible response") from exception

        usage = parsed.get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
        if metrics is not None:
            metrics.record_model_usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        return ModelReply(
            content=str(content),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
