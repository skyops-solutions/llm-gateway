import json

import fakeredis.aioredis
import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import GatewayConfig, Price, Provider, Target, Tenant, hash_key
from app.main import create_app

KEY_A = "key-a"
KEY_B = "key-b"


def make_config(budget_a: float = 1.0) -> GatewayConfig:
    return GatewayConfig(
        providers={
            "primary": Provider("primary", "openai_compat", "http://primary.test/v1"),
            "backup": Provider("backup", "anthropic", "http://backup.test", "BACKUP_KEY"),
        },
        routes={"bot": [Target("primary", "p-model"), Target("backup", "b-model")]},
        prices={"p-model": Price(1.0, 2.0), "b-model": Price(3.0, 15.0)},
        tenants={
            "a": Tenant("a", "Client A", hash_key(KEY_A), ("bot",), budget_a, rate_limit_per_min=5,
                        cache=True, pii_masking=True),
            "b": Tenant("b", "Client B", hash_key(KEY_B), ("bot",), 100.0, rate_limit_per_min=100,
                        cache=False),
        },
    )


class Upstream:
    """Scriptable fake of the two upstream APIs, used through httpx.MockTransport."""

    def __init__(self):
        self.primary_status = 200
        self.backup_status = 200
        self.calls: list[tuple[str, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        host = request.url.host
        self.calls.append((host, body))
        if host == "primary.test":
            if self.primary_status != 200:
                return httpx.Response(self.primary_status, json={"error": "down"})
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "from primary"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
            })
        if self.backup_status != 200:
            return httpx.Response(self.backup_status, json={"error": "down"})
        return httpx.Response(200, json={
            "content": [{"type": "text", "text": "from backup"}], "stop_reason": "end_turn",
            "usage": {"input_tokens": 1000, "output_tokens": 500},
        })


@pytest.fixture
def upstream():
    return Upstream()


@pytest.fixture
def make_client(upstream, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "admin-secret")

    def _make(config: GatewayConfig | None = None) -> TestClient:
        app = create_app(config or make_config(), fakeredis.aioredis.FakeRedis(),
                         httpx.AsyncClient(transport=httpx.MockTransport(upstream.handler)))
        return TestClient(app)

    return _make


def chat(client: TestClient, key: str, content: str = "hello", **extra):
    return client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {key}"},
                       json={"model": "bot", "messages": [{"role": "user", "content": content}], **extra})
