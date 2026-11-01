"""Upstream LLM providers.

openai_compat covers OpenAI, Gemini (OpenAI-compatible endpoint) and Ollama (local open models).
anthropic covers the Claude Messages API.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from .config import Provider


class UpstreamError(Exception):
    def __init__(self, reason: str, retryable: bool = True):
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


@dataclass
class Completion:
    text: str
    input_tokens: int
    output_tokens: int
    finish_reason: str
    upstream_seconds: float


async def complete(client: httpx.AsyncClient, provider: Provider, model: str, body: dict,
                   timeout: float) -> Completion:
    if provider.type == "openai_compat":
        return await _openai_compat(client, provider, model, body, timeout)
    if provider.type == "anthropic":
        return await _anthropic(client, provider, model, body, timeout)
    raise UpstreamError(f"unknown provider type {provider.type}", retryable=False)


async def _post(client: httpx.AsyncClient, url: str, headers: dict, payload: dict,
                timeout: float) -> tuple[dict, float]:
    start = time.perf_counter()
    try:
        resp = await client.post(url, headers=headers, json=payload, timeout=timeout)
    except httpx.TimeoutException as e:
        raise UpstreamError("timeout") from e
    except httpx.HTTPError as e:
        raise UpstreamError("connection") from e
    elapsed = time.perf_counter() - start
    if resp.status_code == 429 or resp.status_code >= 500:
        raise UpstreamError(f"http_{resp.status_code}")
    if resp.status_code >= 400:
        # Bad key or bad request: retrying the same provider will not help, but a fallback might.
        raise UpstreamError(f"http_{resp.status_code}", retryable=False)
    return resp.json(), elapsed


async def _openai_compat(client, provider, model, body, timeout) -> Completion:
    payload = {k: v for k, v in body.items() if k in {"messages", "temperature", "max_tokens", "top_p", "stop"}}
    payload["model"] = model
    headers = {"Authorization": f"Bearer {provider.api_key}"} if provider.api_key else {}
    data, elapsed = await _post(client, f"{provider.base_url.rstrip('/')}/chat/completions",
                                headers, payload, timeout)
    try:
        choice = data["choices"][0]
        usage = data.get("usage") or {}
        return Completion(
            text=choice["message"]["content"] or "",
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            finish_reason=choice.get("finish_reason") or "stop",
            upstream_seconds=elapsed,
        )
    except (KeyError, IndexError, TypeError) as e:
        raise UpstreamError("bad_response") from e


async def _anthropic(client, provider, model, body, timeout) -> Completion:
    messages = body.get("messages", [])
    system = "\n".join(m["content"] for m in messages if m.get("role") == "system")
    payload = {
        "model": model,
        "max_tokens": body.get("max_tokens") or 1024,
        "messages": [m for m in messages if m.get("role") != "system"],
    }
    if system:
        payload["system"] = system
    if "temperature" in body:
        payload["temperature"] = body["temperature"]
    headers = {"x-api-key": provider.api_key, "anthropic-version": "2023-06-01"}
    base = provider.base_url or "https://api.anthropic.com"
    data, elapsed = await _post(client, f"{base.rstrip('/')}/v1/messages", headers, payload, timeout)
    try:
        text = "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        usage = data.get("usage") or {}
        return Completion(
            text=text,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            finish_reason=data.get("stop_reason") or "stop",
            upstream_seconds=elapsed,
        )
    except (KeyError, TypeError) as e:
        raise UpstreamError("bad_response") from e
