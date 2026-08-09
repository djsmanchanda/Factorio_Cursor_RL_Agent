# Path: training/research/llama_client.py
# Purpose: Call a loopback-only llama.cpp OpenAI-compatible endpoint.

from __future__ import annotations

import json
from typing import Callable, Mapping, Sequence
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class LlamaClientError(RuntimeError):
    pass


def _loopback_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("llama.cpp URL must use http or https")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("llama.cpp endpoint must be loopback-only")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("llama.cpp base URL cannot contain credentials, query, or fragment")
    return base_url.rstrip("/")


class LlamaCppClient:
    def __init__(
        self, base_url: str, model: str, *, timeout: float = 300.0,
        opener: Callable = urlopen,
    ) -> None:
        if not model or timeout <= 0:
            raise ValueError("model must be named and timeout must be positive")
        self.base_url = _loopback_url(base_url)
        self.model = model
        self.timeout = timeout
        self._opener = opener

    def complete_json(self, messages: Sequence[Mapping[str, str]]) -> dict:
        payload = {
            "model": self.model,
            "messages": list(messages),
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        request = Request(
            f"{self.base_url}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
            content = result["choices"][0]["message"]["content"]
            proposal = json.loads(content)
        except (KeyError, IndexError, TypeError, ValueError, OSError) as error:
            raise LlamaClientError(f"invalid llama.cpp response: {error}") from error
        if not isinstance(proposal, dict):
            raise LlamaClientError("llama.cpp proposal must be one JSON object")
        return proposal
