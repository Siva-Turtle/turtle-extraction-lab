"""Coverage: Agent Identifier meta-agent (kind == identifier).

The router saves tokens by skipping irrelevant agents: it sees the same
transcript user message plus a candidate roster (name + description, NO
attribute lists, a few uncommon example attributes) and returns the subset
of agent names to run via a strict `agent_selection` json_schema envelope.
"""

import app.modules.runs.router as runs_router
from app.core.openrouter import build_chat_payload
from app.db.models import Agent
from app.modules.runs.router import (
    IDENTIFIER_SCHEMA_NAME,
    _identifier_system_content,
    build_identifier_schema,
    identifier_candidates_with_examples,
)


def _make_agent(client, name, **kw):
    body = {"name": name}
    body.update(kw)
    return client.post("/api/v1/agents", json=body).json()


def _make_attr(client, agent_id, name):
    return client.post("/api/v1/attributes", json={
        "agent_ids": [agent_id], "name": name}).json()


def test_identifier_schema_shape():
    schema = build_identifier_schema()
    assert schema["type"] == "object"
    assert schema["required"] == ["selected_agents"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["selected_agents"] == {
        "type": "array",
        "items": {"type": "string"},
        "description": "Names of the extraction agents to run on this input",
    }
    assert "$ref" not in str(schema)


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


def test_identifier_system_lists_candidates_not_attributes(db):
    ident = Agent(name="agent_identifier", kind="identifier",
                  system_instruction="")
    db.add(ident)
    db.commit()
    system = _identifier_system_content(ident, [
        ("kc_and_feedback", "Karma feedback",
         ["kc_taker_attitude", "kc_taker_readiness", "kc_taker_knowledge"]),
        ("tax_and_insurance", "Tax + cover",
         ["w8_ben"]),
    ])
    assert "Candidate agents" in system
    assert "- kc_and_feedback: Karma feedback" in system
    assert "kc_taker_attitude" in system and "w8_ben" in system
    # Extraction-only contract never rides the router prompt...
    assert runs_router.RESULT_CONTRACT not in system
    # ...and the router returns a name subset (possibly empty).
    assert '"selected_agents"' in system


def test_identifier_preview_branch(client):
    kc = _make_agent(client, "kc_and_feedback",
                     description="Karma feedback",
                     kind="extraction")
    _make_attr(client, kc["id"], "kc_taker_attitude")
    _make_attr(client, kc["id"], "overall_sentiment")
    tax = _make_agent(client, "tax_and_insurance", description="Tax + cover")
    _make_attr(client, tax["id"], "w8_ben")
    _make_attr(client, tax["id"], "advance_tax")
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
    assert "- kc_and_feedback: Karma feedback" in body["system"]
    assert "kc_taker_attitude" in body["system"]  # curated uncommon example
    assert "- tax_and_insurance: Tax + cover" in body["system"]
    assert "w8_ben" in body["system"]
    names = [c["name"] for c in body["candidates"]]
    assert names == ["kc_and_feedback", "tax_and_insurance"]  # self excluded
    assert all("example_attributes" in c for c in body["candidates"])

    # Extraction agents keep the meeting_extraction envelope.
    plain = client.get(f"/api/v1/agents/{kc['id']}/prompt-preview").json()
    assert plain["response_format"]["json_schema"]["name"] == "meeting_extraction"
    assert "selected_agents" not in plain["system"]


def test_identifier_run_path_uses_selection_envelope(client, monkeypatch):
    seen = {}

    async def _capture(payload):
        seen.setdefault("calls", []).append(payload)
        return ({"selected_agents": ["kc_and_feedback"]},
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
    assert body["outputs"][ident] == {"selected_agents": ["kc_and_feedback"]}
    assert len(seen["calls"]) == 1
    call = seen["calls"][0]
    # Same transcript user message as extraction runs — verbatim, no wrapping.
    assert call["messages"][1] == {"role": "user", "content": "advisor was great"}
    assert call["response_format"]["json_schema"]["name"] == "agent_selection"
    assert "- kc_and_feedback: Karma feedback" in call["messages"][0]["content"]
    assert runs_router.RESULT_CONTRACT not in call["messages"][0]["content"]
    # Stored request + denormalized snapshot carry kind/description.
    assert body["requests"][ident] == call
    snap = client.get("/api/v1/logs").json()[0]["agent_snapshot"][ident]
    assert snap["kind"] == "identifier"
    assert snap["description"] == "Router."
    assert snap["attributes"] == []


def test_identifier_run_path_exact_logged_response(client, monkeypatch):
    """Repro: live log row stored {"selected_agents": [5 names incl. "Contact Facts"]}."""
    logged = {"selected_agents": ["basic_info", "behavioral", "goal",
                                  "tax_and_insurance", "Contact Facts"]}

    async def _capture(payload):
        return (dict(logged),
                {"prompt_tokens": 9736, "completion_tokens": 48, "total_tokens": 9784})

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(runs_router, "complete_json_payload", _capture)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)

    ident = _make_agent(client, "agent_identifier", description="Router.",
                        kind="identifier")["id"]
    body = client.post("/api/v1/runs", json={
        "input_type": "transcription", "input_data": "hello",
        "agent_ids": [ident], "model": "m"}).json()
    # Verbatim passthrough — names with spaces/caps preserved, no extraction wrap.
    assert body["outputs"][ident] == logged
    assert body["outputs"][ident]["selected_agents"] == [
        "basic_info", "behavioral", "goal", "tax_and_insurance", "Contact Facts"]


def test_identifier_run_path_empty_list(client, monkeypatch):
    async def _capture(payload):
        return ({"selected_agents": []},
                {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(runs_router, "complete_json_payload", _capture)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)

    ident = _make_agent(client, "agent_identifier", kind="identifier")["id"]
    body = client.post("/api/v1/runs", json={
        "input_type": "transcription", "input_data": "hello",
        "agent_ids": [ident], "model": "m"}).json()
    assert body["outputs"][ident] == {"selected_agents": []}


def test_identifier_candidates_exclude_identifier_kind(client, db):
    _make_agent(client, "plain_one", description="P1")
    _make_agent(client, "agent_identifier", kind="identifier")
    rows = identifier_candidates_with_examples(db)
    assert [n for n, _, _ in rows] == ["plain_one"]


def test_identifier_selection_feedback_saved_to_log(client, monkeypatch):
    async def _capture(payload):
        return ({"selected_agents": ["kc_and_feedback"]},
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
