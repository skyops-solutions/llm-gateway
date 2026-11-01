# LLM Gateway for agencies

A self-hosted gateway between your clients' apps and LLM providers (Claude, OpenAI, Gemini). It gives an agency what raw provider keys do not:

- **Cost per client.** Tokens and calculated USD cost per request, per client, per month.
- **Budget limits.** An alert at 50% of the monthly budget and a hard stop (HTTP 402) at 100%, so one chatbot cannot burn a client's whole AI budget.
- **Provider fallback and circuit breaker.** If the primary provider fails, the request goes to the next one, and a provider that keeps failing is skipped for a while. The client's app sees a normal answer.
- **Cache, rate limits and PII masking** per client.
- **Metrics and dashboards.** Prometheus metrics and a ready Grafana dashboard.

It is an OpenAI-compatible endpoint, so a client app only changes its base URL and API key.

> This is my own project, built to show how I run AI features in production. It is not a client case and has no production users. See [Status](#status) for what was run and what was not.

## Try it in 5 minutes (no API keys, no cost)

Needs Docker. The demo uses a fake LLM provider, so nothing is billed.

```bash
make demo-up        # gateway, Redis, Prometheus, Grafana, mock LLM
curl -s localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer demo-key-northwind-travel" -H 'content-type: application/json' \
  -d '{"model":"support-bot","messages":[{"role":"user","content":"What are your opening hours?"}]}'
```

- Dashboard: http://localhost:3000 (folder "LLM Gateway")
- Admin page: http://localhost:8080/admin (token `demo-admin`)
- Break the primary provider and watch the fallback: `make chaos-on`, then `make chaos-off`
- Load test: `make loadtest RATE=150 DURATION=60s`

## Use it with real providers

1. Copy `.env.example` to `.env`, add your provider keys and an admin token.
2. Edit `config/gateway.yaml`: routes, **prices (fill them in from each provider's price list)**, and one tenant per client.
3. `make up`.

A client key is stored as a SHA-256 hash only. See [docs/runbook.md](docs/runbook.md) for adding clients, alerts and what to do in an outage.

## Measured results (laptop, mock provider)

Full details and caveats in [docs/load-test.md](docs/load-test.md).

| | |
|---|---|
| Throughput without errors, 1 container, 1 worker | 150 requests per second |
| Where it broke | 200 requests per second (122 achieved, 5% errors) on the same laptop |
| Time added by the gateway at 150 RPS | average 2.5 ms, p95 5.7 ms, p99 47 ms |
| Cache hit | p95 4.7 ms |
| Provider outage (primary at 100% errors for 45 s, 100 RPS) | 15,001 requests, 0 failed, p95 413 ms. A first version without a circuit breaker failed 29.6%, see [docs/load-test.md](docs/load-test.md) |

These come from a mock upstream and a laptop that also ran the load generator. Real provider latency will be higher and varies. The gateway's own overhead is the useful number.

## Status

| Part | State |
|---|---|
| Gateway, tests (22 passing), Docker image | Built, tested, run locally |
| docker compose stack with Prometheus and Grafana | Run locally |
| Helm chart on Kubernetes | Deployed to a kind cluster, smoke tested, failed upgrade and rollback tested (`scripts/k8s-smoke.sh`, also runs in CI) |
| Terraform for AWS (`infra/aws`) | **Written and validated, never applied.** It has not run against a real AWS account |
| Real providers (Claude, OpenAI, Gemini) | Request code written for each API, **not tested against the live APIs** |
| Streaming responses | Not supported |

## Layout

```
app/        gateway (FastAPI)
config/     example and demo config
deploy/     Helm chart, Prometheus rules, Grafana dashboard
infra/aws/  Terraform reference module
tools/      mock LLM upstream
loadtest/   k6 script
docs/       architecture, runbook, load test results
```

More: [docs/architecture.md](docs/architecture.md), [docs/runbook.md](docs/runbook.md).
