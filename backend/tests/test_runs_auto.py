"""Auto Select Agents v2: 12 questions -> deterministic routing + consistency v2."""

import copy

import app.modules.runs.router as runs_router
from app.modules.runs.router import (
    IDENTIFIER_QUESTIONS,
    apply_auto_feedback,
    compute_consistency,
)


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


def _setup_minimal_auto(client):
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
    _make_extraction(client, "tax", ["tax_attr"],
                     groups=["Tax"])
    _make_extraction(client, "insurance", ["ins_attr"],
                     groups=["Insurance"])
    ident = _make_identifier(client)
    return ident, beh, qry, kc, fb


def test_compute_consistency_v2_hit_miss_not_scored():
    answers = {q["key"]: False for q in IDENTIFIER_QUESTIONS}
    answers["has_assets"] = True
    plan = [
        {"agent_id": "a1", "agent_name": "asset",
         "reasons": ["has_assets"], "scored": True,
         "attributes": ["holding"]},
        {"agent_id": "a2", "agent_name": "behavioral",
         "reasons": ["always"], "scored": False,
         "attributes": ["b_attr"]},
        {"agent_id": "a3", "agent_name": "income",
         "reasons": ["income"], "scored": True,
         "attributes": ["salary"]},
    ]
    outputs = {
        "ident": dict(answers),
        "a1": {"holding": _filled("house")},
        "a2": {},
        "a3": {"salary": _empty()},
    }
    cons = compute_consistency(answers, outputs, "ident", plan)
    assert cons["auto"] is True
    assert cons["version"] == 2
    assert cons["identifier_agent_id"] == "ident"
    assert cons["answers"] == answers
    assert cons["plan"] == plan
    assert cons["agents"]["a1"]["status"] == "hit"
    assert cons["agents"]["a1"]["score"] == 1.0
    assert cons["agents"]["a3"]["status"] == "miss"
    assert cons["agents"]["a3"]["score"] == 0.0
    assert cons["agents"]["a2"]["status"] == "not_scored"
    assert cons["agents"]["a2"]["score"] is None
    assert cons["score"] == 0.5

    # Errored excluded.
    outputs2 = dict(outputs)
    outputs2["a1"] = {"_error": "boom"}
    cons2 = compute_consistency(answers, outputs2, "ident", plan)
    assert cons2["agents"]["a1"]["status"] == "error"
    assert cons2["score"] == 0.0  # only a3 scored (miss)

    # No scored agents -> None.
    plan3 = [p for p in plan if not p["scored"]]
    cons3 = compute_consistency(answers, {"a2": {}}, "ident", plan3)
    assert cons3["score"] is None


def test_apply_auto_feedback_v2():
    cons = {
        "auto": True, "version": 2, "score": 0.0,
        "identifier_agent_id": "ident",
        "answers": {}, "plan": [],
        "agents": {
            "a1": {"agent_name": "E1", "reasons": ["has_assets"],
                   "scored": True, "extracted": [],
                   "status": "miss", "score": 0.0},
            "a2": {"agent_name": "E2", "reasons": ["always"],
                   "scored": False, "extracted": [],
                   "status": "not_scored", "score": None},
        },
    }
    fb = apply_auto_feedback({}, cons)
    assert fb["E1"]["__agent__"]["rating"] == "down"
    assert fb["E1"]["__agent__"]["auto"] is True
    assert "has_assets" in fb["E1"]["__agent__"]["remarks"]
    assert "E2" not in fb

    # Manual never overwritten.
    manual = {"E1": {"__agent__": {"rating": "up", "remarks": "mine"}}}
    fb2 = apply_auto_feedback(manual, cons)
    assert fb2["E1"]["__agent__"] == {"rating": "up", "remarks": "mine"}

    # Hit removes stale auto.
    cons_hit = copy.deepcopy(cons)
    cons_hit["agents"]["a1"]["status"] = "hit"
    cons_hit["agents"]["a1"]["extracted"] = ["x"]
    fb3 = apply_auto_feedback(fb, cons_hit)
    assert "E1" not in fb3

    # Old shape returns copy unchanged, never crashes.
    assert apply_auto_feedback({"A": {"x": {"rating": "up"}}},
                               {"auto": True, "agents": {}}) == \
        {"A": {"x": {"rating": "up"}}}


def test_auto_all_false_runs_always_only(client, monkeypatch):
    ident, beh, qry, kc, fb = _setup_minimal_auto(client)
    calls = {"n": 0}

    async def _route(payload):
        calls["n"] += 1
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
        if "fb_attr" in required:
            # split feedback agent (all attributes).
            assert "kc_karma" not in required
            assert "kc_fb" not in required
            return ({"fb_attr": _filled("z"), "fb_sent": _filled("w")},
                    {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        raise AssertionError(f"unexpected: {required}")

    monkeypatch.setattr(runs_router, "complete_json_payload", _route)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)

    body = client.post("/api/v1/runs/auto", json={
        "input_type": "mail", "input_data": "hello", "model": "m",
        "meeting_type": "Quarterly Review"}).json()
    assert calls["n"] == 4  # identifier + 3 always-run
    assert set(body["outputs"]) == {ident, beh, qry, fb}
    cons = body["log"]["consistency"]
    assert cons["version"] == 2
    assert cons["answers"] == {q["key"]: False for q in IDENTIFIER_QUESTIONS}
    assert {p["agent_name"] for p in cons["plan"]} == \
        {"behavioral", "query", "feedback"}
    # feedback plan is full (all attributes).
    fb_plan = next(p for p in cons["plan"] if p["agent_name"] == "feedback")
    assert set(fb_plan["attributes"]) == {"fb_attr", "fb_sent"}
    assert cons["score"] is None  # no scored agents
    assert body["log"]["feedback"] == {}


def test_auto_kc_meeting_runs_combined(client, monkeypatch):
    ident, beh, qry, kc, fb = _setup_minimal_auto(client)

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
                    {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        if "q_attr" in required:
            return ({"q_attr": _filled("y")},
                    {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        if "kc_fb" in required:
            assert "kc_karma" in required
            return ({"kc_karma": _filled("k"), "kc_fb": _filled("z")},
                    {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        raise AssertionError(f"unexpected: {required}")

    monkeypatch.setattr(runs_router, "complete_json_payload", _route)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)
    body = client.post("/api/v1/runs/auto", json={
        "input_type": "mail", "input_data": "hello", "model": "m",
        "meeting_type": "Karma Conversation"}).json()
    assert set(body["outputs"]) == {ident, beh, qry, kc}
    cons = body["log"]["consistency"]
    kc_plan = next(p for p in cons["plan"] if p["agent_name"] == "kc_and_feedback")
    assert set(kc_plan["attributes"]) == {"kc_karma", "kc_fb"}
    assert kc_plan["reasons"] == ["always", "meeting:karma_conversation"]


def test_auto_tax_insurance_split_routing(client, monkeypatch):
    ident, beh, qry, kc, fb = _setup_minimal_auto(client)
    tax_comb = _make_extraction(client, "tax_and_insurance",
                                ["ti_ins", "ti_tax"],
                                groups=["Insurance", "Tax"])
    # Split agents already exist from the fixture (tax/insurance); fetch ids.
    agents = {a["name"]: a["id"] for a in client.get("/api/v1/agents").json()}
    tax_id = agents["tax"]
    ins_id = agents["insurance"]

    async def _route(payload):
        rf = payload.get("response_format", {})
        js = rf.get("json_schema", {}) if isinstance(rf, dict) else {}
        if isinstance(js, dict) and js.get("name") == "agent_selection":
            out = {q["key"]: False for q in IDENTIFIER_QUESTIONS}
            out["insurance"] = True
            return (out, {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10})
        schema = {}
        try:
            schema = js.get("schema", {})
        except Exception:
            schema = {}
        required = schema.get("required", []) if isinstance(schema, dict) else []
        if "ins_attr" in required:
            assert "tax_attr" not in required
            assert "ti_ins" not in required
            return ({"ins_attr": _filled("i")},
                    {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10})
        if "b_attr" in required:
            return ({"b_attr": _filled("x")},
                    {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        if "q_attr" in required:
            return ({"q_attr": _filled("y")},
                    {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        if "fb_attr" in required:
            return ({"fb_attr": _filled("z"), "fb_sent": _filled("w")},
                    {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        raise AssertionError(f"unexpected: {required}")

    monkeypatch.setattr(runs_router, "complete_json_payload", _route)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)
    body = client.post("/api/v1/runs/auto", json={
        "input_type": "mail", "input_data": "hello", "model": "m",
        "meeting_type": "Other"}).json()
    assert ins_id in body["outputs"]
    assert tax_comb not in body["outputs"]
    cons = body["log"]["consistency"]
    ins_plan = next(p for p in cons["plan"] if p["agent_name"] == "insurance")
    assert ins_plan["reasons"] == ["insurance"]
    assert ins_plan["attributes"] == ["ins_attr"]


def test_auto_question_routes_and_subset_schema(client, monkeypatch):
    ident, beh, qry, kc, fb = _setup_minimal_auto(client)
    asset = _make_extraction(client, "asset", ["holding"])
    bi = _make_extraction(client, "basic_info",
                          ["cc_attr", "emp_attr"],
                          groups=["Banking", "Employment"])

    async def _route(payload):
        rf = payload.get("response_format", {})
        js = rf.get("json_schema", {}) if isinstance(rf, dict) else {}
        if isinstance(js, dict) and js.get("name") == "agent_selection":
            out = {q["key"]: False for q in IDENTIFIER_QUESTIONS}
            out["has_assets"] = True
            out["credit_cards"] = True
            return (out, {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10})
        schema = {}
        try:
            schema = js.get("schema", {})
        except Exception:
            schema = {}
        required = schema.get("required", []) if isinstance(schema, dict) else []
        if "holding" in required:
            return ({"holding": _filled("house")},
                    {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        if "cc_attr" in required:
            # basic_info subset: only Banking.
            assert "emp_attr" not in required
            return ({"cc_attr": _filled("card")},
                    {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        # always-run agents return empty (miss for scored? they are unscored).
        if "b_attr" in required:
            return ({"b_attr": _filled("x")},
                    {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        if "q_attr" in required:
            return ({"q_attr": _filled("y")},
                    {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        if "fb_attr" in required:
            return ({"fb_attr": _filled("z"), "fb_sent": _filled("w")},
                    {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        raise AssertionError(f"unexpected: {required}")

    monkeypatch.setattr(runs_router, "complete_json_payload", _route)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)

    body = client.post("/api/v1/runs/auto", json={
        "input_type": "mail", "input_data": "hello", "model": "m",
        "meeting_type": "Other"}).json()
    assert set(body["outputs"]) == {ident, beh, qry, fb, asset, bi}
    cons = body["log"]["consistency"]
    assert cons["answers"]["has_assets"] is True
    assert cons["answers"]["credit_cards"] is True
    bi_entry = cons["agents"][bi]
    assert bi_entry["status"] == "hit"
    assert bi_entry["reasons"] == ["credit_cards"]
    asset_entry = cons["agents"][asset]
    assert asset_entry["status"] == "hit"
    assert cons["score"] == 1.0


def test_auto_miss_creates_agent_feedback(client, monkeypatch):
    ident, beh, qry, kc, fb = _setup_minimal_auto(client)
    asset = _make_extraction(client, "asset", ["holding"])

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
    body = client.post("/api/v1/runs/auto", json={
        "input_type": "mail", "input_data": "hello", "model": "m"}).json()
    cons = body["log"]["consistency"]
    assert cons["agents"][asset]["status"] == "miss"
    assert cons["score"] == 0.0
    fb = body["log"]["feedback"]
    assert fb["asset"]["__agent__"]["rating"] == "down"
    assert "has_assets" in fb["asset"]["__agent__"]["remarks"]
    assert "__agent__" not in str(body["outputs"][asset])


def test_auto_identifier_error_runs_always(client, monkeypatch):
    ident, beh, qry, kc, fb = _setup_minimal_auto(client)
    e1 = _make_extraction(client, "asset", ["holding"])

    async def _fail_ident(payload):
        rf = payload.get("response_format", {})
        js = rf.get("json_schema", {}) if isinstance(rf, dict) else {}
        if isinstance(js, dict) and js.get("name") == "agent_selection":
            raise RuntimeError("provider down")
        # always-run agents still run.
        return ({"b_attr": _filled("x")},
                {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    async def _route(payload):
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

    monkeypatch.setattr(runs_router, "complete_json_payload", _route)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)

    body = client.post("/api/v1/runs/auto", json={
        "input_type": "mail", "input_data": "hi", "model": "m"}).json()
    assert "_error" in body["outputs"][ident]
    assert beh in body["outputs"]
    assert qry in body["outputs"]
    assert fb in body["outputs"]
    assert kc not in body["outputs"]
    assert e1 not in body["outputs"]
    cons = body["log"]["consistency"]
    assert cons["version"] == 2
    assert cons["answers"] == {q["key"]: False for q in IDENTIFIER_QUESTIONS}


def test_auto_no_identifier_400(client):
    _make_extraction(client, "lonely", ["email"])
    resp = client.post("/api/v1/runs/auto", json={
        "input_type": "mail", "input_data": "hi", "model": "m"})
    assert resp.status_code == 400


def test_auto_honours_reuse(client, monkeypatch):
    ident, beh, qry, kc, fb = _setup_minimal_auto(client)

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
            return ({"b_attr": _filled("first")},
                    {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        if "q_attr" in required:
            return ({"q_attr": _filled("qy")},
                    {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        return ({"fb_attr": _filled("kz"), "fb_sent": _filled("kw")},
                {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})

    monkeypatch.setattr(runs_router, "complete_json_payload", _route)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)
    first = client.post("/api/v1/runs/auto", json={
        "input_type": "mail", "input_data": "hello", "model": "m"}).json()
    first_log = first["log_id"]

    # Second auto run reuses behavioral; others run fresh.
    async def _second(payload):
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
        # behavioral must NOT be called (reused).
        assert "b_attr" not in required, "reused agent should not run"
        if "q_attr" in required:
            return ({"q_attr": _filled("qy2")},
                    {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10})
        return ({"fb_attr": _filled("kz2"), "fb_sent": _filled("kw2")},
                {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10})

    monkeypatch.setattr(runs_router, "complete_json_payload", _second)
    second = client.post("/api/v1/runs/auto", json={
        "input_type": "mail", "input_data": "hello", "model": "m",
        "reuse": {beh: first_log, "bogus": "missing"}}).json()
    assert second["outputs"][beh] == first["outputs"][beh]
    assert second["usage"]["reused_agents"][beh]["log_id"] == first_log
    assert "reused_from_log_id" in second["usage"]["per_agent"][beh]

    # Invalid reuse ignored (agent runs normally) — covered by bogus above.


def test_retry_on_auto_log_recomputes_v2(client, monkeypatch):
    ident, beh, qry, kc, fb = _setup_minimal_auto(client)
    asset = _make_extraction(client, "asset", ["holding"])

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
    body = client.post("/api/v1/runs/auto", json={
        "input_type": "mail", "input_data": "hello", "model": "m"}).json()
    log_id = body["log_id"]
    assert body["log"]["consistency"]["agents"][asset]["status"] == "miss"
    assert body["log"]["feedback"]["asset"]["__agent__"]["auto"] is True

    async def _retry(payload):
        return ({"holding": _filled("house")},
                {"prompt_tokens": 11, "completion_tokens": 6, "total_tokens": 17})

    monkeypatch.setattr(runs_router, "complete_json_payload", _retry)
    log = client.post(f"/api/v1/runs/logs/{log_id}/retry-agent",
                      json={"agent_id": asset}).json()["log"]
    cons = log["consistency"]
    assert cons["version"] == 2
    assert cons["agents"][asset]["status"] == "hit"
    assert cons["agents"][asset]["extracted"] == ["holding"]
    assert "asset" not in log["feedback"]
