#!/usr/bin/env bash
# Deploy the Helm chart to a throwaway kind cluster, smoke test it, then test a failed upgrade and rollback.
# Used by CI and runnable locally: ./scripts/k8s-smoke.sh
set -euo pipefail

CLUSTER=${CLUSTER:-gw-smoke}
IMAGE=${IMAGE:-llm-gateway:ci}
NS=gw
REL=gw
PF_PORT=18080

cleanup() { kill "${PF_PID:-0}" 2>/dev/null || true; kind delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker build -t "$IMAGE" .
kind create cluster --name "$CLUSTER" --wait 120s
kind load docker-image "$IMAGE" --name "$CLUSTER"

kubectl create namespace $NS
kubectl -n $NS create secret generic llm-gateway-secrets --from-literal=ADMIN_TOKEN=smoke-admin

REPO=${IMAGE%%:*}
TAG=${IMAGE##*:}
helm upgrade --install $REL deploy/helm/llm-gateway -n $NS \
  --set image.repository="$REPO" --set image.tag="$TAG" --set image.pullPolicy=Never \
  --set-file gatewayConfig=config/gateway.demo.yaml \
  --wait --timeout 180s

kubectl -n $NS rollout status deploy/$REL-llm-gateway --timeout=120s
kubectl -n $NS port-forward svc/$REL-llm-gateway $PF_PORT:80 >/dev/null 2>&1 &
PF_PID=$!
for _ in $(seq 1 20); do curl -sf localhost:$PF_PORT/readyz >/dev/null && break; sleep 1; done

echo "--- smoke"
curl -sf localhost:$PF_PORT/healthz
curl -sf localhost:$PF_PORT/readyz
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST localhost:$PF_PORT/v1/chat/completions)
[ "$code" = "401" ] || { echo "expected 401 without key, got $code"; exit 1; }
curl -sf localhost:$PF_PORT/metrics | grep -q '^gateway_requests_total\|^# HELP gateway_requests_total'
echo "smoke ok"

echo "--- failed upgrade must not take the service down"
if helm upgrade $REL deploy/helm/llm-gateway -n $NS --reuse-values --set image.tag=does-not-exist \
     --wait --timeout 40s; then
  echo "upgrade with a bad image unexpectedly succeeded"; exit 1
fi
curl -sf localhost:$PF_PORT/healthz >/dev/null && echo "old pods still serving during the failed rollout"

echo "--- rollback"
helm rollback $REL -n $NS --wait --timeout 120s
kubectl -n $NS rollout status deploy/$REL-llm-gateway --timeout=120s
for _ in $(seq 1 20); do curl -sf localhost:$PF_PORT/readyz >/dev/null && break; sleep 1; done
curl -sf localhost:$PF_PORT/readyz
helm -n $NS history $REL
echo "rollback ok"
