"""Auto Select Agents + consistency pure functions."""

import copy

import app.modules.runs.router as runs_router
from app.modules.runs.router import (
    AUTO_REMARK_MISSED,
    AUTO_REMARK_UNEXPECTED,
    apply_auto_feedback,
    compute_consistency,
)


async def _known_pricing(model):
    return (0.000001, 0.000002)


def _make_extraction(client, name, attrs):
    aid = client.post(
        "/api/v1/agents",
        json={"name": name, "system_instruction": f"sys-{name}"}).json()["id"]
    for attr in attrs:
        client.post("/api/v1/attributes", json={
            "agent_ids": [aid], "name": attr})
    return aid


def _make_identifier(client, name="agent_identifier"):
    return client.post(
        "/api/v1/agents",
        json={"name": name, "description": "Router.", "kind": "identifier"}).json()["id"]


def _filled(value, ctype="quoted"):
    return {"value": value, "confidence": 0.9,
            "confidence_type": ctype, "evidence": "e"}


def _setup_auto_agents(client):
    e1 = _make_extraction(client, "auto_e1", ["email", "phone"])
    e2 = _make_extraction(client, "auto_e2", ["city", "country"])
    e3 = _make_extraction(client, "auto_e3", ["nickname"])
    ident = _make_identifier(client)
    return ident, e1, e2, e3


def test_compute_consistency_unit():
    ident_out = {
        "fillable_attributes": [
            {"agent": "E1", "attributes": ["email"]},
            {"agent": "E2", "attributes": ["city", "country"]},
        ],
        "selected_agents": ["E1", "E2"],
    }
    outputs = {
        "ident": ident_out,
        "a1": {"email": _filled("a@b.in"), "phone": _filled("123")},
        "a2": {"city": _filled("Pune"), "country": {"value": None, "confidence": 0.0,
                                                    "confidence_type": "not_found", "evidence": ""}},
    }
    agents_by_id = {
        "ident": {"name": "IDENT", "kind": "identifier"},
        "a1": {"name": "E1", "kind": "extraction"},
        "a2": {"name": "E2", "kind": "extraction"},
    }
    attrs_by_agent = {
        "ident": [],
        "a1": ["email", "phone"],
        "a2": ["city", "country"],
    }
    cons = compute_consistency(ident_out, outputs, agents_by_id, attrs_by_agent)
    assert cons["identifier_agent_id"] == "ident"
    assert set(cons["agents"]) == {"a1", "a2"}
    assert cons["agents"]["a1"]["predicted"] == ["email"]
    assert cons["agents"]["a1"]["extracted"] == ["email", "phone"]
    assert cons["agents"]["a1"]["missed"] == []
    assert cons["agents"]["a1"]["unexpected"] == ["phone"]
    assert cons["agents"]["a1"]["score"] == 0.5
    assert cons["agents"]["a2"]["predicted"] == ["city", "country"]
    assert cons["agents"]["a2"]["extracted"] == ["city"]
    assert cons["agents"]["a2"]["missed"] == ["country"]
    assert cons["agents"]["a2"]["unexpected"] == []
    assert cons["agents"]["a2"]["score"] == 0.5
    assert cons["score"] == 0.5

    # Both empty -> 1.0; no checked agents -> None.
    cons2 = compute_consistency(
        {"fillable_attributes": [], "selected_agents": []},
        {"ident": {"fillable_attributes": [], "selected_agents": []}},
        {"ident": {"name": "IDENT", "kind": "identifier"}},
        {"ident": []})
    assert cons2["score"] is None
    assert cons2["agents"] == {}
    # Errored agent skipped.
    cons3 = compute_consistency(
        ident_out,
        {"ident": ident_out, "a1": {"_error": "boom"},
         "a2": {"city": _filled("Pune"), "country": _filled("IN")}},
        agents_by_id, attrs_by_agent)
    assert set(cons3["agents"]) == {"a2"}
    assert cons3["agents"]["a2"]["score"] == 1.0
    assert cons3["score"] == 1.0


def test_apply_auto_feedback_unit():
    cons = {
        "score": 0.5, "identifier_agent_id": "ident",
        "agents": {
            "a1": {"agent_name": "E1", "predicted": ["email"],
                   "extracted": ["email", "phone"], "missed": [],
                   "unexpected": ["phone"], "score": 0.5},
            "a2": {"agent_name": "E2", "predicted": ["city", "country"],
                   "extracted": ["city"], "missed": ["country"],
                   "unexpected": [], "score": 0.5},
        },
    }
    fb = apply_auto_feedback({}, cons)
    assert fb["E1"]["phone"] == {"rating": "down", "remarks": AUTO_REMARK_UNEXPECTED, "auto": True}
    assert fb["E2"]["country"] == {"rating": "down", "remarks": AUTO_REMARK_MISSED, "auto": True}

    # Manual entries are never overwritten; stale autos are refreshed.
    manual = {"E1": {"phone": {"rating": "up", "remarks": "looks right"}},
              "E2": {"country": {"rating": "down", "remarks": "auto old", "auto": True},
                     "city": {"rating": "up", "remarks": "mine"}}}
    fb2 = apply_auto_feedback(manual, cons)
    assert fb2["E1"]["phone"] == {"rating": "up", "remarks": "looks right"}
    assert fb2["E2"]["country"]["auto"] is True
    assert fb2["E2"]["city"] == {"rating": "up", "remarks": "mine"}
    # Removing an attr from missed/unexpected drops its auto entry.
    cons_shrunk = copy.deepcopy(cons)
    cons_shrunk["agents"]["a1"]["unexpected"] = []
    cons_shrunk["agents"]["a1"]["missed"] = []
    fb3 = apply_auto_feedback(fb, cons_shrunk)
    assert "E1" not in fb3
    assert fb3["E2"]["country"]["auto"] is True


def test_auto_picks_two_of_three(client, monkeypatch):
    ident, e1, e2, e3 = _setup_auto_agents(client)
    calls = {"n": 0}

    async def _route(payload):
        calls["n"] += 1
        rf = payload.get("response_format", {})
        js = rf.get("json_schema", {}) if isinstance(rf, dict) else {}
        if isinstance(js, dict) and js.get("name") == "agent_selection":
            return ({
                "fillable_attributes": [
                    {"agent": "auto_e1", "attributes": ["email"]},
                    {"agent": "auto_e2", "attributes": ["city", "country"]},
                ],
                "selected_agents": ["auto_e1", "auto_e2"]},
                {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10})
        schema = {}
        try:
            schema = js.get("schema", {})
        except Exception:
            schema = {}
        required = schema.get("required", []) if isinstance(schema, dict) else []
        if "email" in required:
            return ({"email": _filled("a@b.in"), "phone": _filled("999")},
                    {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        if "city" in required:
            return ({"city": _filled("Pune"),
                     "country": {"value": None, "confidence": 0.0,
                                 "confidence_type": "not_found", "evidence": ""}},
                    {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30})
        raise AssertionError(f"unexpected agent call: {required}")

    monkeypatch.setattr(runs_router, "complete_json_payload", _route)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)

    before_logs = len(client.get("/api/v1/logs").json())
    body = client.post("/api/v1/runs/auto", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": ["ignored"], "model": "m"}).json()
    # Identifier + 2 selected = exactly 3 calls; e3 never runs.
    assert calls["n"] == 3
    after_logs = client.get("/api/v1/logs").json()
    assert len(after_logs) == before_logs + 1
    assert set(body["outputs"]) == {ident, e1, e2}
    assert e3 not in body["outputs"]
    assert set(body["requests"]) == {ident, e1, e2}
    assert body["id"] and body["log_id"]
    assert body["log"]["id"] == body["log_id"]
    # Identifier tokens included in totals.
    usage = body["usage"]
    assert usage["prompt_tokens"] == 5 + 10 + 20
    assert set(usage["per_agent"]) == {ident, e1, e2}

    log = body["log"]
    cons = log["consistency"]
    assert cons["identifier_agent_id"] == ident
    assert set(cons["agents"]) == {e1, e2}
    assert cons["agents"][e1]["predicted"] == ["email"]
    assert cons["agents"][e1]["extracted"] == ["email", "phone"]
    assert cons["agents"][e1]["missed"] == []
    assert cons["agents"][e1]["unexpected"] == ["phone"]
    assert cons["agents"][e2]["predicted"] == ["city", "country"]
    assert cons["agents"][e2]["missed"] == ["country"]
    assert cons["agents"][e2]["unexpected"] == []
    assert cons["score"] == 0.5

    fb = log["feedback"]
    assert fb["auto_e1"]["phone"] == {
        "rating": "down", "remarks": AUTO_REMARK_UNEXPECTED, "auto": True}
    assert fb["auto_e2"]["country"] == {
        "rating": "down", "remarks": AUTO_REMARK_MISSED, "auto": True}

    # Manual rating REPLACES the auto entry and drops "auto".
    run_id = body["id"]
    assert client.post(f"/api/v1/runs/{run_id}/feedback", json={
        "agent_name": "auto_e1", "attribute_name": "phone",
        "rating": "up", "remarks": "human says ok"}).json() == {"ok": True}
    logs = {l["run_id"]: l for l in client.get("/api/v1/logs").json()}
    assert logs[run_id]["feedback"]["auto_e1"]["phone"] == {
        "rating": "up", "remarks": "human says ok"}
    assert "auto" not in logs[run_id]["feedback"]["auto_e1"]["phone"]


def test_auto_identifier_error_runs_no_agents(client, monkeypatch):
    ident = _make_identifier(client)
    e1 = _make_extraction(client, "err_e1", ["email"])
    calls = {"n": 0}

    async def _fail_ident(payload):
        calls["n"] += 1
        raise RuntimeError("provider down")

    monkeypatch.setattr(runs_router, "complete_json_payload", _fail_ident)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)

    body = client.post("/api/v1/runs/auto", json={
        "input_type": "mail", "input_data": "hi", "model": "m"}).json()
    assert calls["n"] == 1  # no extraction calls
    assert set(body["outputs"]) == {ident}
    assert "_error" in body["outputs"][ident]
    assert e1 not in body["outputs"]
    assert body["log"]["consistency"]["agents"] == {}


def test_auto_no_identifier_400(client):
    _make_extraction(client, "lonely", ["email"])
    resp = client.post("/api/v1/runs/auto", json={
        "input_type": "mail", "input_data": "hi", "model": "m"})
    assert resp.status_code == 400


def test_retry_on_auto_log_recomputes_consistency(client, monkeypatch):
    ident, e1, e2, _e3 = _setup_auto_agents(client)

    async def _route(payload):
        rf = payload.get("response_format", {})
        js = rf.get("json_schema", {}) if isinstance(rf, dict) else {}
        if isinstance(js, dict) and js.get("name") == "agent_selection":
            return ({
                "fillable_attributes": [
                    {"agent": "auto_e1", "attributes": ["email"]},
                    {"agent": "auto_e2", "attributes": ["city", "country"]},
                ],
                "selected_agents": ["auto_e1", "auto_e2"]},
                {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10})
        schema = {}
        try:
            schema = js.get("schema", {})
        except Exception:
            schema = {}
        required = schema.get("required", []) if isinstance(schema, dict) else []
        if "email" in required:
            return ({"email": _filled("a@b.in"), "phone": _filled("999")},
                    {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        return ({"city": _filled("Pune"),
                 "country": {"value": None, "confidence": 0.0,
                             "confidence_type": "not_found", "evidence": ""}},
                {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30})

    monkeypatch.setattr(runs_router, "complete_json_payload", _route)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)
    body = client.post("/api/v1/runs/auto", json={
        "input_type": "mail", "input_data": "hello", "model": "m"}).json()
    log_id = body["log_id"]
    assert body["log"]["consistency"]["agents"][e1]["unexpected"] == ["phone"]

    # Retry e1 with a clean output (no unexpected phone).
    async def _retry(payload):
        return ({"email": _filled("a@b.in"),
                 "phone": {"value": None, "confidence": 0.0,
                           "confidence_type": "not_found", "evidence": ""}},
                {"prompt_tokens": 11, "completion_tokens": 6, "total_tokens": 17})

    monkeypatch.setattr(runs_router, "complete_json_payload", _retry)
    log = client.post(f"/api/v1/runs/logs/{log_id}/retry-agent",
                      json={"agent_id": e1}).json()["log"]
    cons = log["consistency"]
    assert cons["agents"][e1]["extracted"] == ["email"]
    assert cons["agents"][e1]["unexpected"] == []
    assert cons["agents"][e1]["score"] == 1.0
    # e1 autos gone; e2 autos preserved.
    assert "auto_e1" not in log["feedback"]
    assert log["feedback"]["auto_e2"]["country"]["auto"] is True
