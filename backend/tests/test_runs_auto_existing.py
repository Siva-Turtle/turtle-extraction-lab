"""Auto existing check + auto reuse (v2)."""

import app.modules.runs.router as runs_router
from app.modules.runs.router import IDENTIFIER_QUESTIONS


async def _known_pricing(model):
    return (0.000001, 0.000002)


def _make_extraction(client, name, attrs, groups=None):
    aid = client.post(
        "/api/v1/agents",
        json={"name": name, "system_instruction": f"sys-{name}"}).json()["id"]
    for i, attr in enumerate(attrs):
        g = ""
        if groups and i < len(groups):
            g = groups[i]
        client.post("/api/v1/attributes", json={
            "agent_ids": [aid], "name": attr, "group": g})
    return aid


def _make_identifier(client, name="agent_identifier"):
    return client.post(
        "/api/v1/agents",
        json={"name": name, "description": "Router.", "kind": "identifier"}).json()["id"]


def _filled(value, ctype="quoted"):
    return {"value": value, "confidence": 0.9,
            "confidence_type": ctype, "evidence": "e"}


def _empty():
    return {"value": None, "confidence": 0.0,
            "confidence_type": "not_found", "evidence": ""}


def _setup_auto_agents(client):
    beh = _make_extraction(client, "behavioral", ["b_attr"])
    qry = _make_extraction(client, "query", ["q_attr"])
    kc = _make_extraction(client, "kc_and_feedback",
                          ["kc_karma", "kc_fb"],
                          groups=["Karma Conversation", "Feedback"])
    _make_extraction(client, "kc", ["kc_karma2"],
                     groups=["Karma Conversation"])
    fb = _make_extraction(client, "feedback",
                          ["fb_attr", "fb_sent"],
                          groups=["Feedback", "Sentiment"])
    _make_extraction(client, "tax", ["tax_attr"], groups=["Tax"])
    _make_extraction(client, "insurance", ["ins_attr"],
                     groups=["Insurance"])
    ident = _make_identifier(client)
    return ident, beh, qry, kc, fb


def _auto_route():
    async def _route(payload):
        rf = payload.get("response_format", {})
        js = rf.get("json_schema", {}) if isinstance(rf, dict) else {}
        if isinstance(js, dict) and js.get("name") == "agent_selection":
            out = {q["key"]: False for q in IDENTIFIER_QUESTIONS}
            return (out, {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10})
        schema = {}
        try:
            schema = js.get("schema", {})
        except Exception:
            schema = {}
        required = schema.get("required", []) if isinstance(schema, dict) else []
        if "b_attr" in required:
            return ({"b_attr": _filled("x")},
                    {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        if "q_attr" in required:
            return ({"q_attr": _filled("y")},
                    {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        return ({"fb_attr": _filled("z"), "fb_sent": _filled("w")},
                {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30})
    return _route


def _run_auto(client, model="m", effort="", input_data="hello", meeting_type=""):
    body = {"input_type": "mail", "input_data": input_data,
            "agent_ids": ["ignored"], "model": model,
            "meeting_type": meeting_type}
    if effort:
        body["reasoning_effort"] = effort
    return client.post("/api/v1/runs/auto", json=body).json()


def _check_auto(client, model="m", effort="", input_data="hello", meeting_type=""):
    body = {"input_type": "mail", "input_data": input_data,
            "agent_ids": ["ignored-too"],
            "meeting_type": meeting_type,
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
    assert cons["version"] == 2
    assert cons["identifier_agent_id"]
    # No scored agents -> score None.
    assert cons["score"] is None
    assert set(cons["agents"]) != set()

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
    _make_extraction(client, "behavioral", ["b_attr"])
    _make_extraction(client, "query", ["q_attr"])
    _make_extraction(client, "feedback", ["fb_attr"])

    async def _fail(payload):
        rf = payload.get("response_format", {})
        js = rf.get("json_schema", {}) if isinstance(rf, dict) else {}
        if isinstance(js, dict) and js.get("name") == "agent_selection":
            raise RuntimeError("provider down")
        return ({"b_attr": _filled("x")},
                {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    # Need a route that handles all always-run attrs.
    async def _fail2(payload):
        rf = payload.get("response_format", {})
        js = rf.get("json_schema", {}) if isinstance(rf, dict) else {}
        if isinstance(js, dict) and js.get("name") == "agent_selection":
            raise RuntimeError("provider down")
        schema = {}
        try:
            schema = js.get("schema", {})
        except Exception:
            schema = {}
        required = schema.get("required", []) if isinstance(schema, dict) else []
        if "b_attr" in required:
            return ({"b_attr": _filled("x")},
                    {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        if "q_attr" in required:
            return ({"q_attr": _filled("y")},
                    {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        return ({"fb_attr": _filled("z")},
                {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    monkeypatch.setattr(runs_router, "complete_json_payload", _fail2)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)
    body = _run_auto(client)
    cons = body["log"]["consistency"]
    assert cons["auto"] is True
    assert cons["version"] == 2
    assert cons["identifier_agent_id"] == ident
    assert cons["score"] is None


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
    assert client.get("/api/v1/logs").json() == before


def test_check_existing_auto_returns_always_agents(client, monkeypatch):
    ident, beh, qry, kc, fb = _setup_auto_agents(client)
    monkeypatch.setattr(runs_router, "complete_json_payload", _auto_route())
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)
    _run_auto(client)

    async def _boom(payload):
        raise AssertionError("must not call OpenRouter")

    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)
    data = _check_auto(client)
    slot = data["slots"][0]
    assert slot["log"] is not None
    by_id = {a["agent_id"]: a for a in slot["agents"]}
    # Always-run for a non-KC meeting is feedback (not the combined agent).
    assert set(by_id) == {beh, qry, fb}
    for entry in slot["agents"]:
        assert entry["log_id"]
        assert entry["agent_name"]
        assert "created_at" in entry


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
    # Identifier prompt is code-owned; changing DB instruction must NOT break matching.
    ident, _, _, _, _ = _setup_auto_agents(client)
    monkeypatch.setattr(runs_router, "complete_json_payload", _auto_route())
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)
    _run_auto(client)
    assert _check_auto(client)["slots"][0]["log"] is not None

    async def _boom(payload):
        raise AssertionError("must not call OpenRouter")

    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)
    client.patch(f"/api/v1/agents/{ident}", json={"system_instruction": "changed"})
    # Code-owned prompt ignores DB instruction -> still matches.
    assert _check_auto(client)["slots"][0]["log"] is not None


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
    assert _check_auto(client)["slots"][0]["log"] is None

    monkeypatch.setattr(runs_router, "complete_json_payload", _auto_route())
    created = _run_auto(client)
    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)
    found = _check_auto(client)["slots"][0]["log"]
    assert found is not None
    assert found["id"] == created["log_id"]


def test_check_existing_auto_ignores_identifier_error(client, monkeypatch):
    _make_identifier(client)
    _make_extraction(client, "behavioral", ["b_attr"])
    _make_extraction(client, "query", ["q_attr"])
    _make_extraction(client, "feedback", ["fb_attr"])

    async def _fail(payload):
        rf = payload.get("response_format", {})
        js = rf.get("json_schema", {}) if isinstance(rf, dict) else {}
        if isinstance(js, dict) and js.get("name") == "agent_selection":
            raise RuntimeError("provider down")
        schema = {}
        try:
            schema = js.get("schema", {})
        except Exception:
            schema = {}
        required = schema.get("required", []) if isinstance(schema, dict) else []
        if "b_attr" in required:
            return ({"b_attr": _filled("x")},
                    {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        if "q_attr" in required:
            return ({"q_attr": _filled("y")},
                    {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        return ({"fb_attr": _filled("z"), "fb_sent": _filled("w")},
                {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

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

    # Route where asset misses (to get a __agent__ auto entry).
    _make_extraction(client, "asset", ["holding"])

    async def _route(payload):
        rf = payload.get("response_format", {})
        js = rf.get("json_schema", {}) if isinstance(rf, dict) else {}
        if isinstance(js, dict) and js.get("name") == "agent_selection":
            out = {q["key"]: False for q in IDENTIFIER_QUESTIONS}
            out["has_assets"] = True
            return (out, {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10})
        schema = {}
        try:
            schema = js.get("schema", {})
        except Exception:
            schema = {}
        required = schema.get("required", []) if isinstance(schema, dict) else []
        if "holding" in required:
            return ({"holding": _empty()},
                    {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        if "b_attr" in required:
            return ({"b_attr": _filled("x")},
                    {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        if "q_attr" in required:
            return ({"q_attr": _filled("y")},
                    {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        return ({"fb_attr": _filled("z"), "fb_sent": _filled("w")},
                {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    monkeypatch.setattr(runs_router, "complete_json_payload", _route)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)
    body = _run_auto(client)
    run_id = body["id"]
    log_id = body["log_id"]
    assert body["log"]["feedback"]["asset"]["__agent__"]["auto"] is True

    # Manual rating on __agent__ replaces auto (drops "auto").
    assert client.post(f"/api/v1/runs/{run_id}/feedback", json={
        "agent_name": "asset", "attribute_name": "__agent__",
        "rating": "up", "remarks": "human says ok"}).json()["ok"] is True
    logs = {l["run_id"]: l for l in client.get("/api/v1/logs").json()}
    src = logs[run_id]
    assert src["feedback"]["asset"]["__agent__"] == {
        "rating": "up", "remarks": "human says ok"}
    assert "auto" not in src["feedback"]["asset"]["__agent__"]

    async def _boom(payload):
        raise AssertionError("reuse must not call OpenRouter")

    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)
    reused = client.post("/api/v1/runs/reuse", json={
        "log_id": log_id, "run_group_id": "a" * 32}).json()
    assert reused["log"]["consistency"] == src["consistency"]
    assert reused["log"]["consistency"]["auto"] is True
    fb = reused["log"]["feedback"]
    # Reused rows share the SOURCE feedback (merged, not copied): manual
    # rating surfaces with a source_log_id marker, DB holds no copy.
    assert fb["asset"]["__agent__"]["rating"] == "up"
    assert fb["asset"]["__agent__"]["remarks"] == "human says ok"
    assert fb["asset"]["__agent__"]["source_log_id"] == log_id


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
    src_id = logs[run_id]["id"]
    reused = client.post("/api/v1/runs/reuse", json={
        "log_id": src_id}).json()
    # Display merges the SOURCE feedback (single source of truth).
    assert reused["log"]["feedback"]["M"]["email"]["rating"] == "up"
    assert reused["log"]["feedback"]["M"]["email"]["source_log_id"] == src_id
    assert reused["log"]["consistency"] == {}
