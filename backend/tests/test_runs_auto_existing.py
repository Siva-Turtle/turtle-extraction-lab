"""Auto existing check + auto reuse (spec-be-auto-existing)."""

import app.modules.runs.router as runs_router
from app.modules.runs.router import AUTO_REMARK_MISSED, AUTO_REMARK_UNEXPECTED


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
    ident = _make_identifier(client)
    return ident, e1, e2


def _auto_route():
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
    return _route


def _run_auto(client, model="m", effort="", input_data="hello"):
    body = {"input_type": "mail", "input_data": input_data,
            "agent_ids": ["ignored"], "model": model}
    if effort:
        body["reasoning_effort"] = effort
    return client.post("/api/v1/runs/auto", json=body).json()


def _check_auto(client, model="m", effort="", input_data="hello"):
    body = {"input_type": "mail", "input_data": input_data,
            "agent_ids": ["ignored-too"],
            "models": [{"model": model, "reasoning_effort": effort}]}
    resp = client.post("/api/v1/runs/check-existing-auto", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_auto_consistency_always_marked(client, monkeypatch):
    _setup_auto_agents(client)
    monkeypatch.setattr(runs_router, "complete_json_payload", _auto_route())
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)

    body = _run_auto(client)
    cons = body["log"]["consistency"]
    assert cons["auto"] is True
    assert cons["identifier_agent_id"]
    assert cons["score"] == 0.5

    # Non-auto logs keep consistency {}.
    logs = client.get("/api/v1/logs").json()
    assert len(logs) == 1
    manual_aid = client.post(
        "/api/v1/agents", json={"name": "manual_one"}).json()["id"]
    client.post("/api/v1/attributes", json={
        "agent_ids": [manual_aid], "name": "email"})
    manual = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [manual_aid], "model": "m"}).json()
    assert manual["log"]["consistency"] == {}


def test_auto_identifier_error_still_marked(client, monkeypatch):
    ident = _make_identifier(client)
    _make_extraction(client, "err_e1", ["email"])

    async def _fail(payload):
        raise RuntimeError("provider down")

    monkeypatch.setattr(runs_router, "complete_json_payload", _fail)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)
    body = _run_auto(client)
    cons = body["log"]["consistency"]
    assert cons["auto"] is True
    assert cons["identifier_agent_id"] == ident
    assert cons["score"] is None
    assert cons["agents"] == {}


def test_check_existing_auto_found(client, monkeypatch):
    _setup_auto_agents(client)
    monkeypatch.setattr(runs_router, "complete_json_payload", _auto_route())
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)
    created = _run_auto(client)

    async def _boom(payload):
        raise AssertionError("check-existing-auto must not call OpenRouter")

    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)
    before = client.get("/api/v1/logs").json()
    data = _check_auto(client)
    assert len(data["slots"]) == 1
    slot = data["slots"][0]
    assert slot["model"] == "m"
    assert slot["reasoning_effort"] == ""
    assert slot["log"] is not None
    assert slot["log"]["id"] == created["log_id"]
    assert slot["log"]["consistency"]["auto"] is True
    # Nothing written.
    assert client.get("/api/v1/logs").json() == before


def test_check_existing_auto_different_effort_no_match(client, monkeypatch):
    _setup_auto_agents(client)
    monkeypatch.setattr(runs_router, "complete_json_payload", _auto_route())
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)
    _run_auto(client, effort="")

    async def _boom(payload):
        raise AssertionError("must not call OpenRouter")

    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)
    assert _check_auto(client, effort="high")["slots"][0]["log"] is None
    assert _check_auto(client, model="other")["slots"][0]["log"] is None
    assert _check_auto(client, effort="")["slots"][0]["log"] is not None


def test_check_existing_auto_changed_identifier_prompt_no_match(client, monkeypatch):
    ident, _, _ = _setup_auto_agents(client)
    monkeypatch.setattr(runs_router, "complete_json_payload", _auto_route())
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)
    _run_auto(client)
    assert _check_auto(client)["slots"][0]["log"] is not None

    async def _boom(payload):
        raise AssertionError("must not call OpenRouter")

    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)
    client.patch(f"/api/v1/agents/{ident}", json={"system_instruction": "changed"})
    assert _check_auto(client)["slots"][0]["log"] is None


def test_check_existing_auto_ignores_non_auto_log(client, monkeypatch):
    _setup_auto_agents(client)
    manual_aid = client.post(
        "/api/v1/agents", json={"name": "manual_x"}).json()["id"]
    client.post("/api/v1/attributes", json={
        "agent_ids": [manual_aid], "name": "email"})

    async def _fake(payload):
        return ({"email": _filled("a@b.in")},
                {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    monkeypatch.setattr(runs_router, "complete_json_payload", _fake)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)
    client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [manual_aid], "model": "m"}).json()

    async def _boom(payload):
        raise AssertionError("must not call OpenRouter")

    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)
    # Same input data / model, but only a manual log exists -> no match.
    assert _check_auto(client)["slots"][0]["log"] is None

    # After a real auto run with the same data, the auto log is found.
    monkeypatch.setattr(runs_router, "complete_json_payload", _auto_route())
    created = _run_auto(client)
    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)
    found = _check_auto(client)["slots"][0]["log"]
    assert found is not None
    assert found["id"] == created["log_id"]


def test_check_existing_auto_ignores_identifier_error(client, monkeypatch):
    _make_identifier(client)
    _make_extraction(client, "err_e1", ["email"])

    async def _fail(payload):
        raise RuntimeError("provider down")

    monkeypatch.setattr(runs_router, "complete_json_payload", _fail)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)
    body = _run_auto(client)
    assert body["log"]["consistency"]["auto"] is True

    async def _boom(payload):
        raise AssertionError("must not call OpenRouter")

    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)
    assert _check_auto(client)["slots"][0]["log"] is None


def test_reuse_auto_copies_consistency_and_only_auto_feedback(client, monkeypatch):
    _setup_auto_agents(client)
    monkeypatch.setattr(runs_router, "complete_json_payload", _auto_route())
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)
    body = _run_auto(client)
    run_id = body["id"]
    log_id = body["log_id"]
    assert body["log"]["feedback"]["auto_e1"]["phone"]["auto"] is True
    assert body["log"]["feedback"]["auto_e2"]["country"]["auto"] is True

    # Manual rating REPLACES one auto entry (drops "auto").
    assert client.post(f"/api/v1/runs/{run_id}/feedback", json={
        "agent_name": "auto_e1", "attribute_name": "phone",
        "rating": "up", "remarks": "human says ok"}).json() == {"ok": True}
    logs = {l["run_id"]: l for l in client.get("/api/v1/logs").json()}
    src = logs[run_id]
    assert src["feedback"]["auto_e1"]["phone"] == {
        "rating": "up", "remarks": "human says ok"}
    assert "auto" not in src["feedback"]["auto_e1"]["phone"]
    assert src["feedback"]["auto_e2"]["country"] == {
        "rating": "down", "remarks": AUTO_REMARK_MISSED, "auto": True}

    async def _boom(payload):
        raise AssertionError("reuse must not call OpenRouter")

    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)
    reused = client.post("/api/v1/runs/reuse", json={
        "log_id": log_id, "run_group_id": "a" * 32}).json()
    assert reused["log"]["consistency"] == src["consistency"]
    assert reused["log"]["consistency"]["auto"] is True
    fb = reused["log"]["feedback"]
    # Only auto entries copied; manual rating left behind.
    assert "auto_e1" not in fb
    assert fb == {"auto_e2": {"country": {
        "rating": "down", "remarks": AUTO_REMARK_MISSED, "auto": True}}}


def test_reuse_non_auto_feedback_starts_empty(client, monkeypatch):
    aid = client.post("/api/v1/agents", json={"name": "M"}).json()["id"]
    client.post("/api/v1/attributes", json={"agent_ids": [aid], "name": "email"})

    async def _fake(payload):
        return ({"email": _filled("a@b.in")},
                {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    monkeypatch.setattr(runs_router, "complete_json_payload", _fake)
    run_id = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hi",
        "agent_ids": [aid], "model": "m"}).json()["id"]
    client.post(f"/api/v1/runs/{run_id}/feedback", json={
        "agent_name": "M", "attribute_name": "email",
        "rating": "up", "remarks": "nice"})

    async def _boom(payload):
        raise AssertionError("reuse must not call OpenRouter")

    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)
    logs = {l["run_id"]: l for l in client.get("/api/v1/logs").json()}
    reused = client.post("/api/v1/runs/reuse", json={
        "log_id": logs[run_id]["id"]}).json()
    assert reused["log"]["feedback"] == {}
    assert reused["log"]["consistency"] == {}
