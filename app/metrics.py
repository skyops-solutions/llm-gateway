"""Prometheus metrics exposed on /metrics."""

from prometheus_client import Counter, Gauge, Histogram

LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)
OVERHEAD_BUCKETS = (0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25)

REQUESTS = Counter("gateway_requests_total", "Requests by tenant, route and result",
                   ["tenant", "route", "status"])
DURATION = Histogram("gateway_request_duration_seconds", "End-to-end request time",
                     ["tenant", "route"], buckets=LATENCY_BUCKETS)
OVERHEAD = Histogram("gateway_overhead_seconds", "Time spent in the gateway itself (total minus upstream)",
                     buckets=OVERHEAD_BUCKETS)
UPSTREAM_DURATION = Histogram("gateway_upstream_duration_seconds", "Upstream provider call time",
                              ["provider"], buckets=LATENCY_BUCKETS)
UPSTREAM_ERRORS = Counter("gateway_upstream_errors_total", "Upstream failures", ["provider", "reason"])
FALLBACKS = Counter("gateway_fallback_total", "Requests served by a fallback provider",
                    ["route", "from_provider", "to_provider"])
TOKENS = Counter("gateway_tokens_total", "Tokens by direction", ["tenant", "route", "direction"])
COST = Counter("gateway_cost_usd_total", "Calculated cost in USD", ["tenant", "route"])
UNPRICED = Counter("gateway_unpriced_requests_total", "Requests to models with no price set", ["model"])
CACHE = Counter("gateway_cache_total", "Cache lookups", ["tenant", "result"])
RATE_LIMITED = Counter("gateway_rate_limited_total", "Requests rejected by rate limit", ["tenant"])
BUDGET_RATIO = Gauge("gateway_budget_used_ratio", "Share of monthly budget used (1.0 = 100%)", ["tenant"])
BUDGET_BLOCKED = Counter("gateway_budget_blocked_total", "Requests blocked by budget hard stop", ["tenant"])
PII_MASKED = Counter("gateway_pii_masked_total", "Masked PII items", ["tenant", "kind"])
BREAKER_OPEN = Gauge("gateway_breaker_open", "1 while the circuit breaker skips this provider", ["provider"])
BREAKER_SKIPS = Counter("gateway_breaker_skips_total", "Requests that skipped a provider because its breaker was open",
                        ["provider"])
