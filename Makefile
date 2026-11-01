COMPOSE_DEMO = docker compose -f docker-compose.yml -f docker-compose.demo.yml

.PHONY: up down test lint demo-up demo-down loadtest chaos-on chaos-off helm-lint tf-check

test:
	uv run pytest -q

lint:
	uv run ruff check .

# Production-style stack with your real config and keys from .env
up:
	docker compose up -d --build

down:
	docker compose down -v

# Demo stack: gateway + Redis + Prometheus + Grafana + mock LLM upstream. No keys, no cost.
demo-up:
	$(COMPOSE_DEMO) up -d --build
	@echo "Gateway http://localhost:8080  Admin http://localhost:8080/admin (token: demo-admin)"
	@echo "Grafana http://localhost:3000  Prometheus http://localhost:9090"

demo-down:
	$(COMPOSE_DEMO) down -v

# k6 runs in Docker, so nothing to install. Results are printed and can be pasted into docs/load-test.md
loadtest:
	docker run --rm -i -e BASE_URL=http://host.docker.internal:8080 -e RATE=$${RATE:-200} -e DURATION=$${DURATION:-60s} \
	  -v $(PWD)/loadtest:/scripts grafana/k6:0.57.0 run /scripts/chat.js

# Break the primary provider (100% errors) to watch fallback in Grafana, then restore it.
chaos-on:
	curl -s -XPOST localhost:9000/control/mock-a -H 'content-type: application/json' -d '{"error_rate":1.0}'
chaos-off:
	curl -s -XPOST localhost:9000/control/mock-a -H 'content-type: application/json' -d '{"error_rate":0}'

helm-lint:
	helm lint deploy/helm/llm-gateway && helm template t deploy/helm/llm-gateway > /dev/null

tf-check:
	cd infra/aws && terraform fmt -check -recursive && terraform init -backend=false -input=false > /dev/null && terraform validate
