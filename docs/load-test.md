# Load test results

Date: 2026-11-01. Everything ran on one laptop, so these numbers show the gateway's own limits, not a production setup.

## What was tested

- **Gateway:** 1 container, 1 uvicorn worker, Python 3.12, with Redis in a second container.
- **Upstream:** the mock LLM in `tools/mock_llm.py`, about 300 ms per reply (random 240-360 ms), 120 output tokens. Not a real provider. Real providers will be slower and less steady, so the end-to-end latency below will not match real use. The gateway overhead is the meaningful number.
- **Load generator:** k6 0.57 in Docker, `constant-arrival-rate`, `loadtest/chat.js`.
- **Hardware:** Apple M1, 8 cores, 8 GB RAM. Docker VM: 8 CPUs, 4 GB. k6, gateway, Redis, mock, Prometheus and Grafana all ran on the same machine and competed for CPU.
- **Prompts:** unique per request (no cache) unless marked.

## Results

| Scenario | Target RPS | Achieved RPS | Requests | Failed | p50 | p95 | Notes |
|---|---|---|---|---|---|---|---|
| No cache | 50 | 49.4 | 1,500 | 0% | 308 ms | 365 ms | 30 s |
| No cache | 150 | 149.2 | 9,001 | 0% | 309 ms | 362 ms | 60 s |
| No cache | 200 | 122 | 9,783 | 5.1% | 339 ms | 14.8 s | 60 s. **One container saturated:** requests queued, k6 dropped 2,218 iterations, 496 timed out |
| Cache hits (same prompt) | 150 | 150.0 | 4,500 | 0% | 2.2 ms | 4.7 ms | 30 s. 4,400 hits and 100 misses (the first requests all arrived before the first answer was stored) |

**Gateway overhead** (time in the gateway excluding the upstream call, `gateway_overhead_seconds`) at 150 RPS over 9,001 requests: average 2.5 ms, p50 1.0 ms, p95 5.7 ms, p99 47 ms.

## What this means

- One container with one worker handled 150 RPS with no errors. It broke at 200 RPS on this laptop. Scale by adding replicas (the Helm chart has an HPA, 2 to 6 replicas), because spend, cache and rate limits live in Redis and are shared.
- A cache hit answers in a few milliseconds and costs nothing. Whether it helps depends on how often your users ask the same thing.
- The 99th percentile overhead (47 ms) is much higher than the median. I did not investigate it. Likely causes are Redis round trips and garbage collection, but this is a guess.
- An earlier run at 50 RPS right after the 200 RPS test showed 17 failures out of 1,500. The queue from the overload was still draining. After a gateway restart the same test gave 0 failures. The table shows the clean run.

## Cost per 1,000 requests (calculated, not measured)

Demo prices in `config/gateway.demo.yaml` ($1 per million input tokens, $5 per million output tokens). Average prompt here is about 13 input tokens and the mock returns 120 output tokens:

`(13 x 1.0 + 120 x 5.0) / 1,000,000 x 1,000 = $0.61 per 1,000 requests`

The gateway recorded $17.70 over 28,782 uncached requests in one session, which is $0.615 per 1,000, so the calculation and the accounting agree.

This only shows how the calculation works. Real prices come from the provider's price list, which you put into the config. The gateway counts a model without a price in `gateway_unpriced_requests_total` and bills it as $0, so fill in the prices.

## Provider outage test

Same stack, 100 RPS for 150 s, with the primary mock set to 100% errors from second 50 to second 95 (45 s).

| Version | Requests | Failed | p95 | Notes |
|---|---|---|---|---|
| First version (default HTTP pool of 100 connections, no circuit breaker) | 8,982 | **29.6%** | 60 s | During the outage every request made up to 3 upstream calls, the pool ran out, and requests timed out on **both** providers. k6 dropped 5,485 iterations |
| Current version (pool of 1,000, circuit breaker) | 15,001 | **0%** | 413 ms | Primary skipped after 5 consecutive failures: 6,000 requests went straight to the second provider, 160 upstream errors in total |

What I changed after the first run:
- **HTTP pool** raised from the default 100 to 1,000 connections.
- **Circuit breaker** per provider: after 5 consecutive failures the provider is skipped for 30 s, then one request tests it again. Without it, every request kept paying for the dead provider's retry and wait. Bad requests (400, 404, 422) do not trip it, because they are the caller's fault.

The breaker state is per replica. With several replicas each one learns on its own, which costs a few failed calls per replica at the start of an outage.

Dashboard during this run: [top](img/dashboard-top.jpg), [fallback panels](img/dashboard-fallback.jpg).

## Reproduce

```bash
make demo-up
make loadtest RATE=150 DURATION=60s
make chaos-on     # break the primary provider
make chaos-off
```
