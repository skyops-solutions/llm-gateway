# Runbook

## Add a client
1. Generate a key and its hash:
   ```bash
   KEY="gw_$(openssl rand -hex 24)"; echo "$KEY"
   python -c "import hashlib,sys;print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" "$KEY"
   ```
2. Add a tenant to the config (hash only, never the key), with allowed routes, monthly budget and rate limit.
3. Deploy the new config. Give the client the key once, over a secure channel.

## Deploy and roll back
- Kubernetes: `helm upgrade --install gw deploy/helm/llm-gateway -n gw --set image.tag=<sha> --set-file gatewayConfig=config/gateway.yaml --wait`
- Roll back: `helm rollback gw -n gw --wait`. The chart uses `maxUnavailable: 0`, so a bad image never replaces working pods.
- Test it: `./scripts/k8s-smoke.sh` runs the whole cycle on a throwaway kind cluster.

## Rotate keys
- **Client key:** add a second hash for the client, hand out the new key, remove the old hash after the client switched. (The config takes one hash per client today. Rotate by creating a second client entry with the same limits.)
- **Provider keys:** update the Secret (`llm-gateway-secrets`), then `kubectl rollout restart deploy/<release>-llm-gateway`.

## Alerts
| Alert | Meaning | Do |
|---|---|---|
| `TenantBudgetHalfUsed` | Client used 50% of the monthly budget | Tell the agency account owner. Check the Grafana panel "Monthly budget used" and the tokens panel for a spike |
| `TenantBudgetExhausted` | Client is blocked (HTTP 402) | Raise the budget in config and redeploy, or confirm the block with the client |
| `HighErrorRate` | Over 5% of requests fail on all providers for 5 minutes | Check provider status pages, then the provider keys, then outbound network |
| `ProviderFallbackActive` | Primary is failing, traffic goes to the fallback | Fine for a short time. The fallback may cost more. Check the cost panel |

## Provider outage
1. Grafana panel "Fallbacks and upstream errors" shows which provider fails and why (`http_401` is a key problem, `http_429` is a quota problem, `timeout` is a network or provider problem).
2. Clients keep getting answers through the next provider in the route. Nothing to do unless the fallback is also down.
3. If both are down the gateway returns 502 `upstream_failed`. Tell clients and consider adding a third provider to the route.

## Reset a client's monthly spend (emergency)
Spend resets by itself on the first of the month (UTC). To reset early:
`redis-cli DEL spend:<tenant>:<YYYY-MM>`. Do this only after you agree it with the client, because it changes the numbers you report.
