"""Coverage: Agent Identifier meta-agent (kind == identifier).

The router lists EVERY extraction agent followed by ALL its attributes,
selects an agent when even ONE attribute is fillable, and reports the
fillable attributes per agent via a strict `agent_selection` json_schema
envelope (fillable_attributes first, then selected_agents).
"""

import app.modules.runs.router as runs_router
from app.core.openrouter import build_chat_payload
from app.db.models import Agent
from app.modules.runs.router import (
    IDENTIFIER_SCHEMA_NAME,
    _identifier_system_content,
    build_identifier_schema,
    identifier_candidates_with_attributes,
    normalize_identifier_output,
)


def _make_agent(client, name, **kw):
    body = {"name": name}
    body.update(kw)
    return client.post("/api/v1/agents", json=body).json()


def _make_attr(client, agent_id, name, description=""):
    return client.post("/api/v1/attributes", json={
        "agent_ids": [agent_id], "name": name,
        "description": description or name}).json()


def test_identifier_schema_shape():
    schema = build_identifier_schema()
    assert schema["type"] == "object"
    assert schema["required"] == ["fillable_attributes", "selected_agents"]
    assert schema["additionalProperties"] is False
    # Property ORDER matters — fillable_attributes first.
    assert list(schema["properties"].keys()) == [
        "fillable_attributes", "selected_agents"]
    fillable = schema["properties"]["fillable_attributes"]
    assert fillable["type"] == "array"
    assert fillable["description"] == (
        "Each agent with at least one fillable attribute")
    item = fillable["items"]
    assert item["type"] == "object"
    assert list(item["properties"].keys()) == ["agent", "attributes"]
    assert item["required"] == ["agent", "attributes"]
    assert item["additionalProperties"] is False
    assert item["properties"]["agent"] == {
        "type": "string", "description": "Agent name"}
    assert item["properties"]["attributes"] == {
        "type": "array", "items": {"type": "string"},
        "description": "Fillable attribute names"}
    assert schema["properties"]["selected_agents"] == {
        "type": "array",
        "items": {"type": "string"},
        "description": "Names of the extraction agents to run",
    }
    assert "$ref" not in str(schema)
    # Strict: no nullable/union fields anywhere in the envelope.
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


def test_identifier_system_lists_full_attribute_lists(db):
    ident = Agent(name="agent_identifier", kind="identifier",
                  system_instruction="")
    db.add(ident)
    db.commit()
    system = _identifier_system_content(ident, [
        ("basic_info", "Basic desc",
         [("client_name", "Client name desc"), ("dob", "DOB desc")]),
        ("tax_and_insurance", "Tax + cover",
         [("w8_ben", "W8BEN desc")]),
        ("empty_agent", "Empty",
         []),
    ])
    assert "Candidate agents and their attributes:" in system
    assert "## basic_info: Basic desc" in system
    assert "- client_name: Client name desc" in system
    assert "- dob: DOB desc" in system
    assert "## tax_and_insurance: Tax + cover" in system
    assert "- w8_ben: W8BEN desc" in system
    assert "## empty_agent: Empty" in system
    assert "- (no attributes defined)" in system
    assert "Rules:" in system
    assert "AT LEAST ONE" in system
    assert '"fillable_attributes"' in system
    assert '"selected_agents"' in system
    assert '{"fillable_attributes": [], "selected_agents": []}' in system
    # Extraction-only contract never rides the router prompt...
    assert runs_router.RESULT_CONTRACT not in system


def test_identifier_prompt_lists_every_attribute_of_every_candidate(
        client, db):
    kc = _make_agent(client, "kc_and_feedback",
                     description="Karma feedback",
                     kind="extraction")
    _make_attr(client, kc["id"], "kc_taker_attitude", "Attitude desc")
    _make_attr(client, kc["id"], "overall_sentiment", "Sentiment desc")
    tax = _make_agent(client, "tax_and_insurance", description="Tax + cover")
    _make_attr(client, tax["id"], "w8_ben", "W8BEN desc")
    _make_attr(client, tax["id"], "advance_tax", "Advance tax desc")
    ident = _make_agent(client, "agent_identifier",
                        description="Router.",
                        kind="identifier")["id"]
    ident_row = db.query(Agent).filter(Agent.id == ident).first()
    candidates = identifier_candidates_with_attributes(
        db, exclude_id=ident_row.id)
    by_name = {n: attrs for n, _, attrs in candidates}
    assert set(by_name) == {"kc_and_feedback", "tax_and_insurance"}
    assert [n for n, _, _ in candidates] == [
        "kc_and_feedback", "tax_and_insurance"]  # ordered by name
    assert set(a for a, _ in by_name["kc_and_feedback"]) == {
        "kc_taker_attitude", "overall_sentiment"}
    assert set(a for a, _ in by_name["tax_and_insurance"]) == {
        "w8_ben", "advance_tax"}
    from app.modules.runs.router import _identifier_candidates, _attrs_for_agent
    for cand in _identifier_candidates(db, exclude_id=ident_row.id):
        expected = [(a.name, a.description or "")
                    for a in _attrs_for_agent(db, cand.id)]
        assert (cand.name, cand.description or "", expected) in candidates
    system = _identifier_system_content(ident_row, candidates)
    for name, _, attrs in candidates:
        assert f"## {name}" in system
        for attr_name, attr_desc in attrs:
            assert f"- {attr_name}: {attr_desc}" in system


def test_identifier_preview_branch(client):
    kc = _make_agent(client, "kc_and_feedback",
                     description="Karma feedback",
                     kind="extraction")
    _make_attr(client, kc["id"], "kc_taker_attitude", "Attitude desc")
    _make_attr(client, kc["id"], "overall_sentiment", "Sentiment desc")
    tax = _make_agent(client, "tax_and_insurance", description="Tax + cover")
    _make_attr(client, tax["id"], "w8_ben", "W8BEN desc")
    _make_attr(client, tax["id"], "advance_tax", "Advance tax desc")
    ident = _make_agent(client, "agent_identifier",
                        description="Router.",
                        kind="identifier")["id"]

    body = client.get(f"/api/v1/agents/{ident}/prompt-preview").json()
    assert body["attributes"] == []  # no attribute lists on the router
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "agent_selection", "strict": True,
                        "schema": build_identifier_schema()},
    }
    assert "## kc_and_feedback: Karma feedback" in body["system"]
    assert "- kc_taker_attitude: Attitude desc" in body["system"]
    assert "- overall_sentiment: Sentiment desc" in body["system"]
    assert "## tax_and_insurance: Tax + cover" in body["system"]
    assert "- w8_ben: W8BEN desc" in body["system"]
    assert "- advance_tax: Advance tax desc" in body["system"]
    assert '"fillable_attributes"' in body["system"]
    names = [c["name"] for c in body["candidates"]]
    assert names == ["kc_and_feedback", "tax_and_insurance"]  # self excluded
    for cand in body["candidates"]:
        assert set(cand.keys()) == {"name", "description", "attributes"}
        assert "example_attributes" not in cand
        for attr in cand["attributes"]:
            assert set(attr.keys()) == {"name", "description"}
    kc_cand = next(c for c in body["candidates"]
                   if c["name"] == "kc_and_feedback")
    assert {a["name"] for a in kc_cand["attributes"]} == {
        "kc_taker_attitude", "overall_sentiment"}
    assert {a["name"]: a["description"] for a in kc_cand["attributes"]} == {
        "kc_taker_attitude": "Attitude desc",
        "overall_sentiment": "Sentiment desc"}

    # Extraction agents keep the meeting_extraction envelope.
    plain = client.get(f"/api/v1/agents/{kc['id']}/prompt-preview").json()
    assert plain["response_format"]["json_schema"]["name"] == "meeting_extraction"
    assert "selected_agents" not in plain["system"]


def test_identifier_run_path_uses_selection_envelope(client, monkeypatch):
    seen = {}

    async def _capture(payload):
        seen.setdefault("calls", []).append(payload)
        return ({"fillable_attributes": [
            {"agent": "kc_and_feedback",
             "attributes": ["overall_sentiment"]}],
            "selected_agents": ["kc_and_feedback"]},
            {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6})

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
    assert body["outputs"][ident] == {
        "fillable_attributes": [
            {"agent": "kc_and_feedback",
             "attributes": ["overall_sentiment"]}],
        "selected_agents": ["kc_and_feedback"]}
    assert len(seen["calls"]) == 1
    call = seen["calls"][0]
    # Same transcript user message as extraction runs — verbatim, no wrapping.
    assert call["messages"][1] == {"role": "user", "content": "advisor was great"}
    assert call["response_format"]["json_schema"]["name"] == "agent_selection"
    assert "## kc_and_feedback: Karma feedback" in call["messages"][0]["content"]
    assert "- overall_sentiment" in call["messages"][0]["content"]
    assert runs_router.RESULT_CONTRACT not in call["messages"][0]["content"]
    # Stored request + denormalized snapshot carry kind/description.
    assert body["requests"][ident] == call
    snap = client.get("/api/v1/logs").json()[0]["agent_snapshot"][ident]
    assert snap["kind"] == "identifier"
    assert snap["description"] == "Router."
    assert snap["attributes"] == []


def test_identifier_run_path_normalises_output(client, monkeypatch):
    """Model returns unknown agent/attr: run stores the normalised object."""
    async def _capture(payload):
        return ({"fillable_attributes": [
            {"agent": "kc_and_feedback",
             "attributes": ["overall_sentiment", "nope_unknown"]},
            {"agent": "ghost_agent", "attributes": ["overall_sentiment"]}],
            "selected_agents": ["kc_and_feedback", "ghost_agent"]},
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(runs_router, "complete_json_payload", _capture)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)

    kc = _make_agent(client, "kc_and_feedback", description="Karma feedback")
    _make_attr(client, kc["id"], "overall_sentiment")
    ident = _make_agent(client, "agent_identifier", description="Router.",
                        kind="identifier")["id"]
    body = client.post("/api/v1/runs", json={
        "input_type": "transcription", "input_data": "hello",
        "agent_ids": [ident], "model": "m"}).json()
    assert body["outputs"][ident] == {
        "fillable_attributes": [
            {"agent": "kc_and_feedback",
             "attributes": ["overall_sentiment"]}],
        "selected_agents": ["kc_and_feedback"]}


def test_identifier_run_path_empty_list(client, monkeypatch):
    async def _capture(payload):
        return ({"fillable_attributes": [], "selected_agents": []},
                {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(runs_router, "complete_json_payload", _capture)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)

    ident = _make_agent(client, "agent_identifier", kind="identifier")["id"]
    body = client.post("/api/v1/runs", json={
        "input_type": "transcription", "input_data": "hello",
        "agent_ids": [ident], "model": "m"}).json()
    assert body["outputs"][ident] == {
        "fillable_attributes": [], "selected_agents": []}


def test_normalize_drops_invalid_agent():
    candidates = [("a", "A desc", [("x", "X"), ("y", "Y")])]
    parsed = {"fillable_attributes": [{"agent": "b", "attributes": ["x"]}],
              "selected_agents": ["b"]}
    assert normalize_identifier_output(parsed, candidates) == {
        "fillable_attributes": [], "selected_agents": []}


def test_normalize_drops_unknown_attr_dedupes_keeps_order():
    candidates = [("a", "A desc", [("x", "X"), ("y", "Y")])]
    parsed = {"fillable_attributes": [
        {"agent": "a",
         "attributes": ["y", "unknown", "x", "x", "y", 123]}],
        "selected_agents": []}
    assert normalize_identifier_output(parsed, candidates) == {
        "fillable_attributes": [{"agent": "a", "attributes": ["y", "x"]}],
        "selected_agents": ["a"]}


def test_normalize_selected_agents_unioned():
    candidates = [("a", "A", [("x", "X")]), ("b", "B", [("y", "Y")])]
    parsed = {"fillable_attributes": [{"agent": "b", "attributes": ["y"]}],
              "selected_agents": ["a", "ghost", "a"]}
    assert normalize_identifier_output(parsed, candidates) == {
        "fillable_attributes": [{"agent": "b", "attributes": ["y"]}],
        "selected_agents": ["a", "b"]}
    # Reverse: fillable-only agent joins the model's selection.
    parsed2 = {"fillable_attributes": [{"agent": "a", "attributes": ["x"]}],
               "selected_agents": ["b"]}
    assert normalize_identifier_output(parsed2, candidates) == {
        "fillable_attributes": [{"agent": "a", "attributes": ["x"]}],
        "selected_agents": ["b", "a"]}


def test_normalize_non_dict_untouched():
    for bad in (["x"], "str", None, 42):
        assert normalize_identifier_output(bad, []) is bad
    err = {"_error": "boom"}
    assert normalize_identifier_output(err, []) == {"_error": "boom"}


def test_identifier_candidates_exclude_identifier_kind(client, db):
    _make_agent(client, "plain_one", description="P1")
    _make_agent(client, "agent_identifier", kind="identifier")
    rows = identifier_candidates_with_attributes(db)
    assert [n for n, _, _ in rows] == ["plain_one"]
    assert rows[0][2] == []  # no attributes linked yet


def test_identifier_selection_feedback_saved_to_log(client, monkeypatch):
    async def _capture(payload):
        return ({"fillable_attributes": [
            {"agent": "kc_and_feedback",
             "attributes": ["overall_sentiment"]}],
            "selected_agents": ["kc_and_feedback"]},
            {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(runs_router, "complete_json_payload", _capture)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)

    ident = _make_agent(client, "agent_identifier", description="Router.",
                        kind="identifier")
    body = client.post("/api/v1/runs", json={
        "input_type": "transcription", "input_data": "hello",
        "agent_ids": [ident["id"]], "model": "m"}).json()
    rid = body["id"]

    fb = client.post(f"/api/v1/runs/{rid}/feedback", json={
        "agent_name": ident["name"], "attribute_name": "selected_agents",
        "rating": "up", "remarks": "good routing"}).json()
    assert fb == {"ok": True}
    logs = client.get("/api/v1/logs").json()
    assert logs[0]["feedback"] == {
        ident["name"]: {"selected_agents": {"rating": "up", "remarks": "good routing"}}}
    got = client.get(f"/api/v1/runs/{rid}/feedback").json()
    assert len(got) == 1
    assert (got[0]["agent_name"], got[0]["attribute_name"],
            got[0]["rating"], got[0]["remarks"]) == (
        ident["name"], "selected_agents", "up", "good routing")
