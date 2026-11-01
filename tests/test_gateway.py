from tests.conftest import KEY_A, KEY_B, chat, make_config


def test_rejects_missing_and_wrong_key(make_client):
    with make_client() as c:
        assert c.post("/v1/chat/completions", json={}).status_code == 401
        assert chat(c, "wrong").status_code == 401


def test_forbidden_route(make_client):
    with make_client() as c:
        r = c.post("/v1/chat/completions", headers={"Authorization": f"Bearer {KEY_B}"},
                   json={"model": "other", "messages": [{"role": "user", "content": "x"}]})
        assert r.status_code == 403


def test_primary_success_and_cost(make_client):
    with make_client() as c:
        r = chat(c, KEY_B)
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == "from primary"
        assert r.headers["x-gateway-provider"] == "primary"
        # 1000 in * $1/M + 500 out * $2/M = $0.002
        assert float(r.headers["x-gateway-cost-usd"]) == 0.002


def test_fallback_when_primary_is_down(make_client, upstream):
    upstream.primary_status = 503
    with make_client() as c:
        r = chat(c, KEY_B)
        assert r.status_code == 200
        assert r.headers["x-gateway-provider"] == "backup"
        hosts = [h for h, _ in upstream.calls]
        assert hosts == ["primary.test", "primary.test", "backup.test"]  # primary retried once
        # backup pricing: 1000 * 3/M + 500 * 15/M = 0.0105
        assert float(r.headers["x-gateway-cost-usd"]) == 0.0105
        assert 'gateway_fallback_total{from_provider="primary",route="bot",to_provider="backup"} 1.0' \
            in c.get("/metrics").text


def test_no_retry_on_auth_error_but_fallback(make_client, upstream):
    upstream.primary_status = 401
    with make_client() as c:
        r = chat(c, KEY_B)
        assert r.status_code == 200
        assert [h for h, _ in upstream.calls] == ["primary.test", "backup.test"]


def test_all_providers_down_returns_502(make_client, upstream):
    upstream.primary_status = 500
    upstream.backup_status = 500
    with make_client() as c:
        assert chat(c, KEY_B).status_code == 502


def test_cache_hit_costs_nothing_and_skips_upstream(make_client, upstream):
    with make_client() as c:
        first = chat(c, KEY_A, "same question", temperature=0)
        second = chat(c, KEY_A, "same question", temperature=0)
        assert first.headers["x-cache"] == "MISS"
        assert second.headers["x-cache"] == "HIT"
        assert second.headers["x-gateway-cost-usd"] == "0"
        assert len(upstream.calls) == 1


def test_cache_off_for_tenant(make_client, upstream):
    with make_client() as c:
        chat(c, KEY_B, "q")
        r = chat(c, KEY_B, "q")
        assert r.headers["x-cache"] == "OFF"
        assert len(upstream.calls) == 2


def test_budget_hard_stop(make_client):
    # Budget $0.003: first call costs $0.002, second $0.002 (total 0.004 > budget), third is blocked.
    with make_client(make_config(budget_a=0.003)) as c:
        assert chat(c, KEY_A, "one").status_code == 200
        assert chat(c, KEY_A, "two").status_code == 200
        r = chat(c, KEY_A, "three")
        assert r.status_code == 402
        assert r.json()["error"]["type"] == "budget_exceeded"


def test_budget_is_per_tenant(make_client):
    with make_client(make_config(budget_a=0.001)) as c:
        chat(c, KEY_A, "one")
        assert chat(c, KEY_A, "two").status_code == 402
        assert chat(c, KEY_B, "two").status_code == 200


def test_rate_limit(make_client):
    with make_client() as c:
        codes = [chat(c, KEY_A, f"q{i}").status_code for i in range(7)]
        assert codes[:5] == [200] * 5
        assert codes[5:] == [429, 429]


def test_pii_masked_before_upstream(make_client, upstream):
    with make_client() as c:
        chat(c, KEY_A, "Call me at +380 67 123 45 67 or mail jane.doe@example.com")
        sent = upstream.calls[0][1]["messages"][0]["content"]
        assert "[PHONE]" in sent and "[EMAIL]" in sent
        assert "example.com" not in sent and "123 45 67" not in sent


def test_pii_not_masked_when_disabled(make_client, upstream):
    with make_client() as c:
        chat(c, KEY_B, "mail jane.doe@example.com")
        assert "jane.doe@example.com" in upstream.calls[0][1]["messages"][0]["content"]


def test_anthropic_payload_moves_system_prompt(make_client, upstream):
    upstream.primary_status = 503
    with make_client() as c:
        c.post("/v1/chat/completions", headers={"Authorization": f"Bearer {KEY_B}"},
               json={"model": "bot", "messages": [{"role": "system", "content": "be brief"},
                                                  {"role": "user", "content": "hi"}]})
        backup_body = upstream.calls[-1][1]
        assert backup_body["system"] == "be brief"
        assert all(m["role"] != "system" for m in backup_body["messages"])


def test_admin_summary_requires_token_and_reports_spend(make_client):
    with make_client() as c:
        chat(c, KEY_B)
        assert c.get("/admin/api/summary").status_code == 401
        r = c.get("/admin/api/summary", headers={"Authorization": "Bearer admin-secret"})
        b = next(t for t in r.json()["tenants"] if t["tenant"] == "b")
        assert b["requests"] == 1 and b["spent_usd"] == 0.002


def test_health_and_models(make_client):
    with make_client() as c:
        assert c.get("/healthz").json() == {"status": "ok"}
        assert c.get("/readyz").status_code == 200
        r = c.get("/v1/models", headers={"Authorization": f"Bearer {KEY_A}"})
        assert [m["id"] for m in r.json()["data"]] == ["bot"]


def test_streaming_rejected(make_client):
    with make_client() as c:
        assert chat(c, KEY_B, stream=True).status_code == 400


def test_breaker_stops_calling_a_failing_provider(make_client, upstream):
    upstream.primary_status = 503
    with make_client() as c:
        for i in range(3):  # 2 primary calls each (one retry) = 6 failures, breaker opens at 5
            assert chat(c, KEY_B, f"q{i}").status_code == 200
        primary_calls = sum(1 for h, _ in upstream.calls if h == "primary.test")
        assert chat(c, KEY_B, "after open").status_code == 200
        assert sum(1 for h, _ in upstream.calls if h == "primary.test") == primary_calls  # skipped


def test_bad_request_does_not_trip_breaker(make_client, upstream):
    upstream.primary_status = 400
    with make_client() as c:
        for i in range(8):
            chat(c, KEY_B, f"q{i}")
        assert not c.app.state.breaker.is_open("primary")
