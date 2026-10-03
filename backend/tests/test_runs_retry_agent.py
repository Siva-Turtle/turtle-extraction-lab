"""Per-agent retry: one call, patch in place, totals, feedback clear, 404/400."""

import copy

import app.modules.runs.router as runs_router


async def _known_pricing(model):
    return (0.000001, 0.000002)


def _make_agent(client, name, attrs):
    aid = client.post(
        "/api/v1/agents",
        json={"name": name, "system_instruction": f"sys-{name}"}).json()["id"]
    for attr in attrs:
        client.post("/api/v1/attributes", json={
            "agent_ids": [aid], "name": attr})
    return aid


def _out_email(val):
    return {"email": {"value": val, "confidence": 0.9,
                      "confidence_type": "quoted", "evidence": "e"}}


def _out_phone(val):
    return {"phone": {"value": val, "confidence": 0.8,
                      "confidence_type": "quoted", "evidence": "e"}}


def test_retry_updates_only_target_agent(client, monkeypatch):
    a1 = _make_agent(client, "R1", ["email"])
    a2 = _make_agent(client, "R2", ["phone"])
    calls = {"n": 0}

    async def _initial(payload):
        calls["n"] += 1
        text = payload["messages"][0]["content"]
        if "sys-R1" in text:
            return (_out_email("old@x.in"),
                    {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        assert "sys-R2" in text
        return (_out_phone("111"),
                {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30})

    monkeypatch.setattr(runs_router, "complete_json_payload", _initial)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)

    body = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [a1, a2], "model": "m"}).json()
    assert calls["n"] == 2
    log_id = body["log_id"]
    run_id = body["id"]
    old_requests = copy.deepcopy(body["requests"])
    old_a2_out = copy.deepcopy(body["outputs"][a2])

    # Feedback for both agents.
    client.post(f"/api/v1/runs/{run_id}/feedback", json={
        "agent_name": "R1", "attribute_name": "email",
        "rating": "up", "remarks": "good"})
    client.post(f"/api/v1/runs/{run_id}/feedback", json={
        "agent_name": "R2", "attribute_name": "phone",
        "rating": "down", "remarks": "bad"})

    retry_calls = {"n": 0}
    seen_payloads = []

    async def _retry(payload):
        retry_calls["n"] += 1
        seen_payloads.append(copy.deepcopy(payload))
        return (_out_email("new@x.in"),
                {"prompt_tokens": 30, "completion_tokens": 15, "total_tokens": 45})

    monkeypatch.setattr(runs_router, "complete_json_payload", _retry)

    resp = client.post(f"/api/v1/runs/logs/{log_id}/retry-agent",
                       json={"agent_id": a1})
    assert resp.status_code == 200, resp.text
    assert retry_calls["n"] == 1
    # Re-sent EXACTLY the stored body.
    assert seen_payloads[0] == old_requests[a1]

    log = resp.json()["log"]
    assert log["id"] == log_id
    # Only that agent updated.
    assert log["outputs"][a1]["email"]["value"] == "new@x.in"
    assert log["outputs"][a2] == old_a2_out
    # Requests untouched.
    assert log["requests"] == old_requests
    # Totals recomputed as sum of per_agent.
    per = log["usage"]["per_agent"]
    assert set(per) == {a1, a2}
    assert per[a1]["prompt_tokens"] == 30
    assert per[a1]["completion_tokens"] == 15
    assert per[a2]["prompt_tokens"] == 20
    assert log["usage"]["prompt_tokens"] == 50
    assert log["usage"]["completion_tokens"] == 25
    assert log["usage"]["total_tokens"] == 75
    # Pricing applied to the fresh entry.
    assert per[a1]["cost_usd"] == round(30 * 0.000001 + 15 * 0.000002, 6)
    # Feedback for that agent cleared, other preserved.
    assert log["feedback"] == {"R2": {"phone": {"rating": "down", "remarks": "bad"}}}
    # Same log row (no new log), Run row mirrored.
    assert len(client.get("/api/v1/logs").json()) == 1
    detail = client.get(f"/api/v1/runs/{run_id}").json()
    assert detail["outputs"][a1]["email"]["value"] == "new@x.in"
    assert detail["outputs"][a2] == old_a2_out


def test_retry_404_and_400(client, monkeypatch):
    a1 = _make_agent(client, "R404", ["email"])

    async def _ok(payload):
        return (_out_email("x"), {"prompt_tokens": 1, "completion_tokens": 1,
                                  "total_tokens": 2})

    monkeypatch.setattr(runs_router, "complete_json_payload", _ok)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)
    log_id = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hi",
        "agent_ids": [a1], "model": "m"}).json()["log_id"]

    async def _boom(payload):
        raise AssertionError("must not call OpenRouter on 404/400")

    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)
    assert client.post("/api/v1/runs/logs/missing/retry-agent",
                       json={"agent_id": a1}).status_code == 404
    assert client.post(f"/api/v1/runs/logs/{log_id}/retry-agent",
                       json={"agent_id": "nope"}).status_code == 400
    assert client.post(f"/api/v1/runs/logs/{log_id}/retry-agent",
                       json={"agent_id": ""}).status_code == 400
    assert client.post(f"/api/v1/runs/logs/{log_id}/retry-agent",
                       json={}).status_code == 400


def test_retry_drops_reused_markers(client, monkeypatch):
    a1 = _make_agent(client, "RR1", ["email"])
    a2 = _make_agent(client, "RR2", ["phone"])

    async def _first(payload):
        text = payload["messages"][0]["content"]
        if "sys-RR1" in text:
            return (_out_email("a@b.in"),
                    {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        return (_out_phone("1"),
                {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})

    monkeypatch.setattr(runs_router, "complete_json_payload", _first)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)
    first = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [a1, a2], "model": "m"}).json()
    first_log = first["log_id"]

    # Second run reuses R1 from the first log, fresh R2 (no OpenRouter for R1).
    async def _second(payload):
        text = payload["messages"][0]["content"]
        assert "sys-RR2" in text  # only R2 runs fresh
        return (_out_phone("2"),
                {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10})

    monkeypatch.setattr(runs_router, "complete_json_payload", _second)
    second = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [a1, a2], "model": "m",
        "reuse": {a1: first_log}}).json()
    assert second["usage"]["reused_agents"] != {}
    assert "reused_from_log_id" in second["usage"]["per_agent"][a1]
    second_log = second["log_id"]

    async def _retry(payload):
        return (_out_email("fresh@b.in"),
                {"prompt_tokens": 4, "completion_tokens": 4, "total_tokens": 8})

    monkeypatch.setattr(runs_router, "complete_json_payload", _retry)
    log = client.post(f"/api/v1/runs/logs/{second_log}/retry-agent",
                      json={"agent_id": a1}).json()["log"]
    per = log["usage"]["per_agent"][a1]
    assert "reused_from_log_id" not in per
    assert "reused_from_created_at" not in per
    assert a1 not in log["usage"].get("reused_agents", {})
    assert log["outputs"][a1]["email"]["value"] == "fresh@b.in"
