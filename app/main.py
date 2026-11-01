"""LLM gateway: one OpenAI-compatible endpoint in front of several LLM providers."""

from __future__ import annotations

import json
import logging
import os
import secrets
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis.asyncio import Redis

from . import metrics as m
from .breaker import Breaker
from .config import GatewayConfig, Tenant, load_config
from .pii import mask_messages
from .providers import Completion, UpstreamError, complete
from .store import Store


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname,
                "msg": record.getMessage()}
        data.update(getattr(record, "extra_fields", {}))
        return json.dumps(data, ensure_ascii=False)


log = logging.getLogger("gateway")
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(JsonFormatter())
log.handlers = [_handler]
log.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
log.propagate = False


def event(msg: str, **fields) -> None:
    log.info(msg, extra={"extra_fields": fields})


UPSTREAM_TIMEOUT = float(os.environ.get("UPSTREAM_TIMEOUT_SECONDS", "60"))
RETRIES_PRIMARY = int(os.environ.get("RETRIES_PRIMARY", "1"))


def create_app(config: GatewayConfig | None = None, redis: Redis | None = None,
               http: httpx.AsyncClient | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.config = config or load_config(os.environ.get("GATEWAY_CONFIG", "config/gateway.yaml"))
        app.state.store = Store(redis or Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0")),
                                cache_ttl=int(os.environ.get("CACHE_TTL_SECONDS", "3600")))
        app.state.http = http or httpx.AsyncClient(
            limits=httpx.Limits(max_connections=1000, max_keepalive_connections=200))
        app.state.breaker = Breaker(int(os.environ.get("BREAKER_THRESHOLD", "5")),
                                    float(os.environ.get("BREAKER_COOLDOWN_SECONDS", "30")))
        # Publish the budget gauge for every client at startup, so dashboards show them before any traffic.
        for t in app.state.config.tenants.values():
            m.BUDGET_RATIO.labels(t.id).set(await app.state.store.spent_usd(t.id) / t.monthly_budget_usd)
        yield
        if http is None:
            await app.state.http.aclose()

    app = FastAPI(title="LLM Gateway", version="0.1.0", lifespan=lifespan)

    def tenant_from(authorization: str | None) -> Tenant:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "missing bearer token")
        tenant = app.state.config.tenant_for_key(authorization[7:].strip())
        if tenant is None:
            raise HTTPException(401, "invalid api key")
        return tenant

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz():
        try:
            await app.state.store.r.ping()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(503, "redis unavailable") from e
        return {"status": "ready"}

    @app.get("/metrics")
    async def metrics_endpoint():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/v1/models")
    async def list_models(authorization: str | None = Header(None)):
        tenant = tenant_from(authorization)
        return {"object": "list", "data": [{"id": r, "object": "model"} for r in tenant.allowed_routes]}

    @app.post("/v1/chat/completions")
    async def chat(request: Request, authorization: str | None = Header(None)):
        t0 = time.perf_counter()
        req_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        tenant = tenant_from(authorization)
        cfg: GatewayConfig = app.state.config
        store: Store = app.state.store
        body = await request.json()
        route = body.get("model", "")

        if body.get("stream"):
            raise HTTPException(400, "streaming is not supported yet")
        if route not in tenant.allowed_routes:
            m.REQUESTS.labels(tenant.id, route, "forbidden").inc()
            raise HTTPException(403, f"model {route!r} is not allowed for this key")
        if not isinstance(body.get("messages"), list) or not body["messages"]:
            raise HTTPException(400, "messages must be a non-empty list")

        if not await store.allow(tenant.id, tenant.rate_limit_per_min):
            m.RATE_LIMITED.labels(tenant.id).inc()
            m.REQUESTS.labels(tenant.id, route, "rate_limited").inc()
            return JSONResponse({"error": {"type": "rate_limited", "message": "rate limit exceeded"}},
                                status_code=429, headers={"retry-after": "60"})

        spent = await store.spent_usd(tenant.id)
        m.BUDGET_RATIO.labels(tenant.id).set(spent / tenant.monthly_budget_usd)
        if spent >= tenant.monthly_budget_usd:
            m.BUDGET_BLOCKED.labels(tenant.id).inc()
            m.REQUESTS.labels(tenant.id, route, "budget_exceeded").inc()
            event("budget_exceeded", req_id=req_id, tenant=tenant.id, spent_usd=round(spent, 6))
            return JSONResponse({"error": {"type": "budget_exceeded",
                                           "message": "monthly budget for this key is used up"}},
                                status_code=402)

        if tenant.pii_masking:
            masked, counts = mask_messages(body["messages"])
            body = {**body, "messages": masked}
            for kind, n in counts.items():
                if n:
                    m.PII_MASKED.labels(tenant.id, kind).inc(n)

        cache_key = store.cache_key(tenant.id, route, body) if tenant.cache else None
        if cache_key:
            cached = await store.cache_get(cache_key)
            if cached:
                m.CACHE.labels(tenant.id, "hit").inc()
                m.REQUESTS.labels(tenant.id, route, "ok").inc()
                await store.add_usage(tenant.id, 0.0, "ok", cache_hit=True)
                elapsed = time.perf_counter() - t0
                m.DURATION.labels(tenant.id, route).observe(elapsed)
                m.OVERHEAD.observe(elapsed)
                event("request", req_id=req_id, tenant=tenant.id, route=route, cache="hit",
                      ms=round(elapsed * 1000, 1))
                return JSONResponse(cached, headers={"x-cache": "HIT", "x-request-id": req_id,
                                                     "x-gateway-cost-usd": "0"})
            m.CACHE.labels(tenant.id, "miss").inc()

        result, used_target, upstream_total = None, None, 0.0
        targets = cfg.routes[route]
        last_error = "no_targets"
        breaker: Breaker = app.state.breaker
        for i, target in enumerate(targets):
            provider = cfg.providers[target.provider]
            if not breaker.allow(provider.name) and i < len(targets) - 1:
                m.BREAKER_SKIPS.labels(provider.name).inc()
                continue  # provider is known to be failing, go straight to the next target
            attempts = 1 + (RETRIES_PRIMARY if i == 0 else 0)
            for _ in range(attempts):
                try:
                    with m.UPSTREAM_DURATION.labels(provider.name).time():
                        result = await complete(app.state.http, provider, target.model, body, UPSTREAM_TIMEOUT)
                    upstream_total += result.upstream_seconds
                    used_target = target
                    breaker.success(provider.name)
                    break
                except UpstreamError as e:
                    last_error = e.reason
                    m.UPSTREAM_ERRORS.labels(provider.name, e.reason).inc()
                    event("upstream_error", req_id=req_id, tenant=tenant.id, provider=provider.name,
                          reason=e.reason)
                    # Bad requests (400, 404, 422) are the caller's fault and must not trip the breaker.
                    if (e.retryable or e.reason in ("http_401", "http_403")) and breaker.failure(provider.name):
                        event("breaker_open", provider=provider.name, cooldown_s=breaker.cooldown)
                    m.BREAKER_OPEN.labels(provider.name).set(1 if breaker.is_open(provider.name) else 0)
                    if not e.retryable:
                        break
            if result is not None:
                m.BREAKER_OPEN.labels(provider.name).set(1 if breaker.is_open(provider.name) else 0)
                if i > 0:
                    m.FALLBACKS.labels(route, targets[0].provider, target.provider).inc()
                    event("fallback", req_id=req_id, route=route, from_provider=targets[0].provider,
                          to_provider=target.provider)
                break

        if result is None:
            m.REQUESTS.labels(tenant.id, route, "upstream_failed").inc()
            await store.add_usage(tenant.id, 0.0, "upstream_failed", cache_hit=False)
            return JSONResponse({"error": {"type": "upstream_failed", "message": last_error}}, status_code=502,
                                headers={"x-request-id": req_id})

        cost = calc_cost(cfg, used_target.model, result)
        new_spent = await store.add_usage(tenant.id, cost, "ok", cache_hit=False)
        m.BUDGET_RATIO.labels(tenant.id).set(new_spent / tenant.monthly_budget_usd)
        m.TOKENS.labels(tenant.id, route, "input").inc(result.input_tokens)
        m.TOKENS.labels(tenant.id, route, "output").inc(result.output_tokens)
        m.COST.labels(tenant.id, route).inc(cost)
        m.REQUESTS.labels(tenant.id, route, "ok").inc()

        response = {
            "id": f"chatcmpl-{req_id}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": route,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": result.text},
                         "finish_reason": result.finish_reason}],
            "usage": {"prompt_tokens": result.input_tokens, "completion_tokens": result.output_tokens,
                      "total_tokens": result.input_tokens + result.output_tokens},
        }
        if cache_key:
            await store.cache_set(cache_key, response)

        elapsed = time.perf_counter() - t0
        m.DURATION.labels(tenant.id, route).observe(elapsed)
        m.OVERHEAD.observe(max(elapsed - upstream_total, 0.0))
        event("request", req_id=req_id, tenant=tenant.id, route=route, provider=used_target.provider,
              model=used_target.model, cache="miss" if cache_key else "off",
              in_tokens=result.input_tokens, out_tokens=result.output_tokens, cost_usd=round(cost, 8),
              ms=round(elapsed * 1000, 1), upstream_ms=round(upstream_total * 1000, 1))
        return JSONResponse(response, headers={"x-cache": "MISS" if cache_key else "OFF", "x-request-id": req_id,
                                               "x-gateway-provider": used_target.provider,
                                               "x-gateway-cost-usd": f"{cost:.8f}"})

    # Admin --------------------------------------------------------------
    def check_admin(authorization: str | None) -> None:
        expected = os.environ.get("ADMIN_TOKEN", "")
        given = (authorization or "")[7:].strip() if (authorization or "").lower().startswith("bearer ") else ""
        if not expected or not secrets.compare_digest(given, expected):
            raise HTTPException(401, "admin token required")

    @app.get("/admin/api/summary")
    async def admin_summary(authorization: str | None = Header(None)):
        check_admin(authorization)
        cfg: GatewayConfig = app.state.config
        out = []
        for t in cfg.tenants.values():
            spent = await app.state.store.spent_usd(t.id)
            usage = await app.state.store.usage(t.id)
            out.append({"tenant": t.id, "name": t.name, "budget_usd": t.monthly_budget_usd,
                        "spent_usd": round(spent, 6), "budget_used": round(spent / t.monthly_budget_usd, 4),
                        "requests": usage.get("requests", 0), "cache_hits": usage.get("cache_hits", 0),
                        "failed": usage.get("status_upstream_failed", 0), "routes": list(t.allowed_routes)})
        return {"tenants": out}

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_page():
        return (Path(__file__).parent / "admin.html").read_text()

    return app


def calc_cost(cfg: GatewayConfig, model: str, result: Completion) -> float:
    price = cfg.prices.get(model)
    if price is None or price.input_per_mtok is None or price.output_per_mtok is None:
        m.UNPRICED.labels(model).inc()
        return 0.0
    return (result.input_tokens * price.input_per_mtok + result.output_tokens * price.output_per_mtok) / 1_000_000


app = create_app()
