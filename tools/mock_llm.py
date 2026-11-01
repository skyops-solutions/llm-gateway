"""Fake LLM upstream for demos, load tests and fallback tests. Costs nothing, needs no keys.

Paths: /{name}/v1/chat/completions (OpenAI format) and /{name}/v1/messages (Anthropic format).
Control: POST /control/{name} {"error_rate": 0..1, "latency_ms": int}. GET /control shows the state.
Token counts are derived from the prompt length (4 chars per token) and a fixed output size.
"""

from __future__ import annotations

import asyncio
import os
import random

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

OUTPUT_TOKENS = int(os.environ.get("MOCK_OUTPUT_TOKENS", "120"))
DEFAULT_LATENCY_MS = int(os.environ.get("MOCK_LATENCY_MS", "300"))

app = FastAPI(title="mock-llm")
state: dict[str, dict] = {}


def cfg(name: str) -> dict:
    return state.setdefault(name, {"error_rate": 0.0, "latency_ms": DEFAULT_LATENCY_MS, "calls": 0})


async def behave(name: str) -> JSONResponse | None:
    s = cfg(name)
    s["calls"] += 1
    await asyncio.sleep(s["latency_ms"] / 1000 * random.uniform(0.8, 1.2))
    if random.random() < s["error_rate"]:
        return JSONResponse({"error": "mock failure"}, status_code=503)
    return None


def prompt_tokens(messages: list[dict]) -> int:
    return max(1, sum(len(str(m.get("content", ""))) for m in messages) // 4)


@app.post("/{name}/v1/chat/completions")
async def openai_format(name: str, request: Request):
    if (failure := await behave(name)) is not None:
        return failure
    body = await request.json()
    return {"choices": [{"message": {"role": "assistant", "content": f"Mock answer from {name}."},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt_tokens(body.get("messages", [])), "completion_tokens": OUTPUT_TOKENS}}


@app.post("/{name}/v1/messages")
async def anthropic_format(name: str, request: Request):
    if (failure := await behave(name)) is not None:
        return failure
    body = await request.json()
    return {"content": [{"type": "text", "text": f"Mock answer from {name}."}], "stop_reason": "end_turn",
            "usage": {"input_tokens": prompt_tokens(body.get("messages", [])), "output_tokens": OUTPUT_TOKENS}}


@app.post("/control/{name}")
async def control(name: str, request: Request):
    body = await request.json()
    s = cfg(name)
    if "error_rate" in body:
        s["error_rate"] = float(body["error_rate"])
    if "latency_ms" in body:
        s["latency_ms"] = int(body["latency_ms"])
    return s


@app.get("/control")
async def control_state():
    return state
