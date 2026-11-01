"""Redis-backed state: monthly spend, usage counters, response cache and rate limits."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from redis.asyncio import Redis

MICRO = 1_000_000  # spend is stored as integer micro-USD to avoid float drift


def month_key(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y-%m")


class Store:
    def __init__(self, redis: Redis, cache_ttl: int = 3600):
        self.r = redis
        self.cache_ttl = cache_ttl

    # Spend and usage -----------------------------------------------------
    async def spent_usd(self, tenant: str) -> float:
        v = await self.r.get(f"spend:{tenant}:{month_key()}")
        return int(v or 0) / MICRO

    async def add_usage(self, tenant: str, cost_usd: float, status: str, cache_hit: bool) -> float:
        m = month_key()
        pipe = self.r.pipeline()
        pipe.incrby(f"spend:{tenant}:{m}", round(cost_usd * MICRO))
        pipe.hincrby(f"usage:{tenant}:{m}", "requests", 1)
        pipe.hincrby(f"usage:{tenant}:{m}", f"status_{status}", 1)
        if cache_hit:
            pipe.hincrby(f"usage:{tenant}:{m}", "cache_hits", 1)
        res = await pipe.execute()
        return int(res[0]) / MICRO

    async def usage(self, tenant: str) -> dict[str, int]:
        raw = await self.r.hgetall(f"usage:{tenant}:{month_key()}")
        return {(k.decode() if isinstance(k, bytes) else k): int(v) for k, v in raw.items()}

    # Rate limit (fixed one-minute window) ---------------------------------
    async def allow(self, tenant: str, limit: int) -> bool:
        window = int(datetime.now(UTC).timestamp() // 60)
        key = f"rl:{tenant}:{window}"
        count = await self.r.incr(key)
        if count == 1:
            await self.r.expire(key, 70)
        return count <= limit

    # Cache ---------------------------------------------------------------
    @staticmethod
    def cache_key(tenant: str, route: str, body: dict) -> str:
        material = json.dumps(
            {"t": tenant, "r": route, "m": body.get("messages"), "temp": body.get("temperature"),
             "max": body.get("max_tokens")},
            sort_keys=True, ensure_ascii=False,
        )
        return "cache:" + hashlib.sha256(material.encode()).hexdigest()

    async def cache_get(self, key: str) -> dict | None:
        v = await self.r.get(key)
        return json.loads(v) if v else None

    async def cache_set(self, key: str, value: dict) -> None:
        await self.r.set(key, json.dumps(value), ex=self.cache_ttl)
