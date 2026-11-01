"""Gateway configuration: providers, routes, prices and tenants from one YAML file."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Provider:
    name: str
    type: str  # "openai_compat" or "anthropic"
    base_url: str = ""
    api_key_env: str = ""

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "") if self.api_key_env else ""


@dataclass(frozen=True)
class Target:
    provider: str
    model: str


@dataclass(frozen=True)
class Price:
    input_per_mtok: float | None
    output_per_mtok: float | None


@dataclass(frozen=True)
class Tenant:
    id: str
    name: str
    key_sha256: str
    allowed_routes: tuple[str, ...]
    monthly_budget_usd: float
    rate_limit_per_min: int = 60
    cache: bool = True
    pii_masking: bool = False


@dataclass
class GatewayConfig:
    providers: dict[str, Provider]
    routes: dict[str, list[Target]]
    prices: dict[str, Price]
    tenants: dict[str, Tenant]
    _by_key: dict[str, Tenant] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._by_key = {t.key_sha256: t for t in self.tenants.values()}
        for route, targets in self.routes.items():
            for t in targets:
                if t.provider not in self.providers:
                    raise ValueError(f"route {route!r} uses unknown provider {t.provider!r}")
        for t in self.tenants.values():
            for r in t.allowed_routes:
                if r not in self.routes:
                    raise ValueError(f"tenant {t.id!r} allows unknown route {r!r}")

    def tenant_for_key(self, api_key: str) -> Tenant | None:
        return self._by_key.get(hash_key(api_key))


def hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


def load_config(path: str | Path) -> GatewayConfig:
    raw = yaml.safe_load(Path(path).read_text())
    providers = {
        name: Provider(name=name, type=p["type"], base_url=p.get("base_url", ""),
                       api_key_env=p.get("api_key_env", ""))
        for name, p in raw["providers"].items()
    }
    routes = {
        name: [Target(provider=t["provider"], model=t["model"]) for t in targets]
        for name, targets in raw["routes"].items()
    }
    prices = {
        model: Price(p.get("input_per_mtok"), p.get("output_per_mtok"))
        for model, p in (raw.get("prices") or {}).items()
    }
    tenants = {
        tid: Tenant(
            id=tid,
            name=t["name"],
            key_sha256=t["key_sha256"],
            allowed_routes=tuple(t["allowed_routes"]),
            monthly_budget_usd=float(t["monthly_budget_usd"]),
            rate_limit_per_min=int(t.get("rate_limit_per_min", 60)),
            cache=bool(t.get("cache", True)),
            pii_masking=bool(t.get("pii_masking", False)),
        )
        for tid, t in raw["tenants"].items()
    }
    return GatewayConfig(providers=providers, routes=routes, prices=prices, tenants=tenants)
