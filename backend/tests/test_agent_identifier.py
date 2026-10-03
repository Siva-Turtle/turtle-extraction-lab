"""Coverage: Agent Identifier 11 yes/no questions + deterministic routing."""

import app.modules.runs.router as runs_router
from app.core.openrouter import build_chat_payload
from app.db.models import Agent
from app.modules.runs.router import (
    IDENTIFIER_QUESTIONS,
    IDENTIFIER_INSTRUCTION,
    IDENTIFIER_SCHEMA_NAME,
    _identifier_system_content,
    build_identifier_schema,
    normalize_identifier_output,
    plan_auto_agents,
)


def _make_agent(client, name, **kw):
    body = {"name": name}
    body.update(kw)
    return client.post("/api/v1/agents", json=body).json()


def _make_attr(client, agent_id, name, description="", group=""):
    return client.post("/api/v1/attributes", json={
        "agent_ids": [agent_id], "name": name,
        "description": description or name, "group": group}).json()


def test_identifier_questions_order_and_text():
    keys = [q["key"] for q in IDENTIFIER_QUESTIONS]
    assert keys == [
        "has_assets", "has_accounts", "credit_cards", "employment_changed",
        "alumni", "expenses", "goals", "income", "insurance",
        "liabilities", "tax",
    ]
    by_key = {q["key"]: q["question"] for q in IDENTIFIER_QUESTIONS}
    assert by_key["has_assets"] == (
        "Did the Client mention about their assets? (Any mention of asset type like - "
        "bonds, cash, commodity, ETFs, mutual funds, crypto, debt instruments, deposits, "
        "equity, personal debt that they have given to someone, real estate or property, "
        "REITS, unlisted stocks or any other asset type)")
    assert by_key["credit_cards"] == (
        "Did the Client mention that they have or don't have credit card(s)?")
    assert by_key["tax"] == (
        'Did the Client mention anything related to "Advance Tax, Rental TDS, '
        'tax filing in India, tax filing outside India, GST services, W8 BEN"')


def test_identifier_schema_shape():
    schema = build_identifier_schema()
    assert schema["type"] == "object"
    expected_keys = [q["key"] for q in IDENTIFIER_QUESTIONS]
    assert list(schema["properties"].keys()) == expected_keys
    assert schema["required"] == expected_keys
    assert schema["additionalProperties"] is False
    for q in IDENTIFIER_QUESTIONS:
        prop = schema["properties"][q["key"]]
        assert prop == {"type": "boolean", "description": q["question"]}
    assert "$ref" not in str(schema)
    assert "null" not in str(schema)


def test_envelope_builder_schema_name_branch():
    inner = build_identifier_schema()
    default = build_chat_payload(model="m", system="s", user="u",
                                 json_schema=inner)
    assert default["response_format"]["json_schema"]["name"] == "meeting_extraction"
    routed = build_chat_payload(model="m", system="s", user="u",
                                json_schema=inner,
                                schema_name=IDENTIFIER_SCHEMA_NAME)
    assert routed["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "agent_selection", "strict": True,
                        "schema": inner},
    }


def test_identifier_system_content_code_owned():
    ident = Agent(name="agent_identifier", kind="identifier",
                  system_instruction="DB IGNORED custom")
    system = _identifier_system_content(ident)
    # DB instruction ignored.
    assert "DB IGNORED" not in system
    assert "Turtle Finance" in system
    assert "advisors" in system.lower() or "advisor" in system.lower()
    assert "true or false" in system.lower()
    assert "CLIENT" in system
    assert "strictly" in system.lower()
    # Numbered list Q1..Q11.
    for i, q in enumerate(IDENTIFIER_QUESTIONS, start=1):
        assert f"Q{i} ({q['key']}): {q['question']}" in system
    # No candidate roster.
    assert "Candidate agents" not in system
    assert runs_router.RESULT_CONTRACT not in system


def test_identifier_preview_branch(client):
    kc = _make_agent(client, "kc_and_feedback", description="Karma feedback",
                     kind="extraction")
    _make_attr(client, kc["id"], "kc_attr", group="Karma Conversation")
    ident = _make_agent(client, "agent_identifier", description="Router.",
                        kind="identifier")["id"]

    body = client.get(f"/api/v1/agents/{ident}/prompt-preview").json()
    assert body["attributes"] == []
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "agent_selection", "strict": True,
                        "schema": build_identifier_schema()},
    }
    assert "Turtle Finance" in body["system"]
    assert "Q1 (has_assets)" in body["system"]
    assert "Q11 (tax)" in body["system"]
    assert body["candidates"] == []
    assert len(body["questions"]) == 11

    plain = client.get(f"/api/v1/agents/{kc['id']}/prompt-preview").json()
    assert plain["response_format"]["json_schema"]["name"] == "meeting_extraction"


def test_identifier_run_path_uses_boolean_envelope(client, monkeypatch):
    seen = {}

    async def _capture(payload):
        seen.setdefault("calls", []).append(payload)
        out = {q["key"]: False for q in IDENTIFIER_QUESTIONS}
        out["has_assets"] = True
        return (out, {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6})

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(runs_router, "complete_json_payload", _capture)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)

    kc = _make_agent(client, "kc_and_feedback", description="Karma feedback")
    _make_attr(client, kc["id"], "overall_sentiment")
    ident = _make_agent(client, "agent_identifier", description="Router.",
                        kind="identifier")["id"]

    body = client.post("/api/v1/runs", json={
        "input_type": "transcription", "input_data": "advisor was great",
        "agent_ids": [ident], "model": "m"}).json()
    assert body["outputs"][ident]["has_assets"] is True
    assert body["outputs"][ident]["tax"] is False
    assert len(seen["calls"]) == 1
    call = seen["calls"][0]
    assert call["messages"][1] == {"role": "user", "content": "advisor was great"}
    assert call["response_format"]["json_schema"]["name"] == "agent_selection"
    assert "Q1 (has_assets)" in call["messages"][0]["content"]
    assert "Turtle Finance" in call["messages"][0]["content"]
    snap = client.get("/api/v1/logs").json()[0]["agent_snapshot"][ident]
    assert snap["kind"] == "identifier"
    assert snap["attributes"] == []


def test_normalize_boolean_answers():
    parsed = {"has_assets": True, "insurance": "TrUe", "tax": "FALSE",
              "income": 1, "goals": "yes"}
    out = normalize_identifier_output(parsed)
    assert out["has_assets"] is True
    assert out["insurance"] is True
    assert out["tax"] is False
    assert out["income"] is False
    assert out["goals"] is False
    # Missing keys become False.
    assert out["liabilities"] is False
    assert set(out.keys()) == {q["key"] for q in IDENTIFIER_QUESTIONS}


def test_normalize_non_dict_untouched():
    for bad in (["x"], "str", None, 42):
        assert normalize_identifier_output(bad) is bad
    err = {"_error": "boom"}
    assert normalize_identifier_output(err) == {"_error": "boom"}
    # Old two-arg call still works (second arg ignored).
    assert normalize_identifier_output({"has_assets": True}, [])["has_assets"] is True


# --- plan_auto_agents unit tests (fake Agent/Attribute objects) ---

class _FakeAgent:
    def __init__(self, name, aid=None, kind="extraction", is_enabled=True):
        self.name = name
        self.id = aid or f"id-{name}"
        self.kind = kind
        self.is_enabled = is_enabled


class _FakeAttr:
    def __init__(self, aid, group=""):
        self.id = aid
        self.group_name = group


def _plan_setup(extra_attrs=None, disabled=None):
    names = ["behavioral", "query", "kc_and_feedback", "basic_info",
             "asset", "account", "expense", "goal", "income",
             "liability", "tax_and_insurance"]
    by_name = {}
    attrs_by_name = {}
    for n in names:
        is_en = False if disabled == n else True
        by_name[n] = _FakeAgent(n, is_enabled=is_en)
        attrs_by_name[n] = [_FakeAttr(f"{n}-a1"), _FakeAttr(f"{n}-a2")]
    # kc_and_feedback groups.
    attrs_by_name["kc_and_feedback"] = [
        _FakeAttr("kc-karma", "Karma Conversation"),
        _FakeAttr("kc-feedback", "Feedback"),
        _FakeAttr("kc-sent", "Sentiment"),
    ]
    # basic_info groups.
    attrs_by_name["basic_info"] = [
        _FakeAttr("bi-bank", "Banking"),
        _FakeAttr("bi-emp", "Employment"),
        _FakeAttr("bi-alu", "Education / Alumni"),
        _FakeAttr("bi-other", "Other"),
    ]
    # tax_and_insurance groups.
    attrs_by_name["tax_and_insurance"] = [
        _FakeAttr("ti-ins", "Insurance"),
        _FakeAttr("ti-tax", "Tax"),
        _FakeAttr("ti-comp", "Tax / Compliance"),
        _FakeAttr("ti-other", "Other"),
    ]
    if extra_attrs:
        for k, v in extra_attrs.items():
            attrs_by_name[k] = v
    return by_name, attrs_by_name


def _false_answers():
    return {q["key"]: False for q in IDENTIFIER_QUESTIONS}


def _plan_names(plan):
    return [e["agent"].name for e in plan]


def test_plan_all_false_only_always():
    by_name, attrs_by = _plan_setup()
    plan = plan_auto_agents(_false_answers(), "Some Review", by_name, attrs_by)
    assert _plan_names(plan) == ["behavioral", "query", "kc_and_feedback"]
    # kc without Karma group.
    kc = next(e for e in plan if e["agent"].name == "kc_and_feedback")
    assert kc["reasons"] == ["always"]
    assert kc["scored"] is False
    assert set(kc["attribute_ids"]) == {"kc-feedback", "kc-sent"}
    for e in plan:
        assert e["scored"] is False


def test_plan_kc_meeting_full():
    by_name, attrs_by = _plan_setup()
    plan = plan_auto_agents(_false_answers(), "Karma Conversation with Client",
                            by_name, attrs_by)
    assert _plan_names(plan) == ["behavioral", "query", "kc_and_feedback", "basic_info"]
    kc = next(e for e in plan if e["agent"].name == "kc_and_feedback")
    assert kc["attribute_ids"] is None
    assert kc["reasons"] == ["always", "meeting:karma_conversation"]
    bi = next(e for e in plan if e["agent"].name == "basic_info")
    assert bi["attribute_ids"] is None
    assert bi["reasons"] == ["meeting:karma_conversation"]
    assert bi["scored"] is False


def test_plan_kickoff_meeting():
    by_name, attrs_by = _plan_setup()
    for mt in ["Kick-off Call", "kickoff meeting", "Kick Off session"]:
        plan = plan_auto_agents(_false_answers(), mt, by_name, attrs_by)
        assert "basic_info" in _plan_names(plan)
        bi = next(e for e in plan if e["agent"].name == "basic_info")
        assert bi["attribute_ids"] is None
        assert bi["reasons"] == ["meeting:kick_off"]
        kc = next(e for e in plan if e["agent"].name == "kc_and_feedback")
        assert set(kc["attribute_ids"]) == {"kc-feedback", "kc-sent"}


def test_plan_employment_only_subset():
    by_name, attrs_by = _plan_setup()
    ans = _false_answers()
    ans["employment_changed"] = True
    plan = plan_auto_agents(ans, "Quarterly Review", by_name, attrs_by)
    bi = next(e for e in plan if e["agent"].name == "basic_info")
    assert bi["attribute_ids"] == ["bi-emp"]
    assert bi["reasons"] == ["employment_changed"]
    assert bi["scored"] is True
    kc = next(e for e in plan if e["agent"].name == "kc_and_feedback")
    assert set(kc["attribute_ids"]) == {"kc-feedback", "kc-sent"}


def test_plan_credit_cards_only():
    by_name, attrs_by = _plan_setup()
    ans = _false_answers()
    ans["credit_cards"] = True
    plan = plan_auto_agents(ans, "Other", by_name, attrs_by)
    bi = next(e for e in plan if e["agent"].name == "basic_info")
    assert bi["attribute_ids"] == ["bi-bank"]
    assert bi["reasons"] == ["credit_cards"]


def test_plan_insurance_only_tax_only_both():
    by_name, attrs_by = _plan_setup()
    ans = _false_answers()
    ans["insurance"] = True
    plan = plan_auto_agents(ans, "Other", by_name, attrs_by)
    ti = next(e for e in plan if e["agent"].name == "tax_and_insurance")
    assert ti["attribute_ids"] == ["ti-ins"]
    assert ti["reasons"] == ["insurance"]

    ans2 = _false_answers()
    ans2["tax"] = True
    plan2 = plan_auto_agents(ans2, "Other", by_name, attrs_by)
    ti2 = next(e for e in plan2 if e["agent"].name == "tax_and_insurance")
    assert set(ti2["attribute_ids"]) == {"ti-tax", "ti-comp"}
    assert ti2["reasons"] == ["tax"]

    ans3 = _false_answers()
    ans3["insurance"] = True
    ans3["tax"] = True
    plan3 = plan_auto_agents(ans3, "Other", by_name, attrs_by)
    ti3 = next(e for e in plan3 if e["agent"].name == "tax_and_insurance")
    assert ti3["attribute_ids"] is None
    assert ti3["reasons"] == ["insurance", "tax"]


def test_plan_disabled_agent_skipped():
    by_name, attrs_by = _plan_setup(disabled="behavioral")
    plan = plan_auto_agents(_false_answers(), "Other", by_name, attrs_by)
    assert "behavioral" not in _plan_names(plan)
    assert "query" in _plan_names(plan)

    by_name2, attrs_by2 = _plan_setup(disabled="asset")
    ans = _false_answers()
    ans["has_assets"] = True
    plan2 = plan_auto_agents(ans, "Other", by_name2, attrs_by2)
    assert "asset" not in _plan_names(plan2)


def test_plan_group_matching_case_insensitive_strip():
    by_name, attrs_by = _plan_setup()
    attrs_by["basic_info"] = [_FakeAttr("x1", "  BANKING  ")]
    ans = _false_answers()
    ans["credit_cards"] = True
    plan = plan_auto_agents(ans, "Other", by_name, attrs_by)
    bi = next(e for e in plan if e["agent"].name == "basic_info")
    assert bi["attribute_ids"] == ["x1"]
