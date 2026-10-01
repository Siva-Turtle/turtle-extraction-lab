"""Coverage: test runs (OpenRouter stubbed), feedback, denormalized log snapshots."""

import app.modules.runs.router as runs_router
from app.modules.runs.router import build_extraction_schema


async def _fake_complete(payload):
    assert payload["model"] == "test-model"
    return ({"email": {"value": "a@b.in", "confidence": 0.9,
                      "confidence_type": "quoted", "evidence": "mail me at a@b.in"}},
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})


def _setup(client, monkeypatch):
    monkeypatch.setattr(runs_router, "complete_json_payload", _fake_complete)
    aid = client.post("/api/v1/agents", json={"name": "A"}).json()["id"]
    client.post("/api/v1/attributes", json={"agent_ids": [aid], "name": "email"})
    return aid


def test_run_and_feedback_flow(client, monkeypatch):
    aid = _setup(client, monkeypatch)
    run = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": [aid], "model": "test-model"}).json()
    assert run["outputs"][aid]["email"]["value"] == "a@b.in"
    rid = run["id"]

    assert client.post("/api/v1/runs", json={
        "input_type": "sms", "input_data": "x",
        "agent_ids": [aid], "model": "m"}).status_code == 422

    detail = client.get(f"/api/v1/runs/{rid}").json()
    assert detail["input_data"] == "mail me at a@b.in"
    assert client.get("/api/v1/runs/missing").status_code == 404

    fb = client.post(f"/api/v1/runs/{rid}/feedback", json={
        "agent_name": "A", "attribute_name": "email",
        "rating": "up", "remarks": "exact quote"}).json()
    assert fb == {"ok": True}
    assert client.post(f"/api/v1/runs/{rid}/feedback",
                       json={"rating": "meh"}).status_code == 422
    got = client.get(f"/api/v1/runs/{rid}/feedback").json()
    assert [(f["rating"], f["remarks"]) for f in got] == [("up", "exact quote")]

    # Log row carries frozen snapshots + merged feedback.
    logs = client.get("/api/v1/logs").json()
    assert len(logs) == 1
    assert logs[0]["run_id"] == rid
    assert logs[0]["agent_snapshot"][aid]["name"] == "A"
    assert logs[0]["feedback"] == {"A": {"email": {"rating": "up", "remarks": "exact quote"}}}


def test_run_without_key_records_error(client):
    aid = client.post("/api/v1/agents", json={"name": "B"}).json()["id"]
    run = client.post("/api/v1/runs", json={
        "input_type": "messages", "input_data": "hi",
        "agent_ids": [aid], "model": "m"}).json()
    assert "_error" in run["outputs"][aid]  # no OPENROUTER_API_KEY in test env


def test_run_shared_attribute_reaches_both_agents(client, monkeypatch):
    seen = {}

    async def _capture(payload):
        seen.setdefault("calls", []).append(payload)
        return ({"ok": 1}, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    monkeypatch.setattr(runs_router, "complete_json_payload", _capture)
    aid1 = client.post("/api/v1/agents", json={"name": "A1", "system_instruction": "sys1"}).json()["id"]
    aid2 = client.post("/api/v1/agents", json={"name": "A2", "system_instruction": "sys2"}).json()["id"]
    client.post("/api/v1/attributes", json={
        "agent_ids": [aid1, aid2], "name": "mood", "type": "enum",
        "description": "Caller mood", "enum_values": ["good", "bad"]})

    body = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [aid1, aid2], "model": "m"}).json()
    assert set(body["outputs"]) == {aid1, aid2}
    assert len(seen["calls"]) == 2
    for call in seen["calls"]:
        user_text = call["messages"][1]["content"]
        # enum options ride along in the user prompt...
        assert "- mood (enum: good | bad): Caller mood" in user_text
        # ...and in the structured schema envelope.
        props = call["response_format"]["json_schema"]["schema"]["properties"]
        assert props["mood"] == {"type": "string", "description": "Caller mood",
                                 "enum": ["good", "bad"]}

    logs = client.get("/api/v1/logs").json()
    snap = logs[0]["agent_snapshot"]
    assert snap[aid1]["attributes"] == snap[aid2]["attributes"] == [
        {"name": "mood", "type": "enum", "description": "Caller mood",
         "enum_values": ["good", "bad"]}]
    assert "prompt" not in snap[aid1]  # dormant column never snapshotted


def test_build_extraction_schema_mapping():
    from app.db.models import Attribute

    def _attr(name, type_, desc="", enum_values=None):
        return Attribute(name=name, type=type_, description=desc,
                         enum_values=enum_values or [])

    schema = build_extraction_schema([
        _attr("s", "string", "A string"),
        _attr("n", "number", "A number"),
        _attr("b", "boolean", "A bool"),
        _attr("e", "enum", "Pick one", ["x", "y"]),
    ])
    assert schema["type"] == "object"
    assert schema["required"] == []
    assert schema["additionalProperties"] is False
    assert schema["properties"]["s"] == {"type": "string", "description": "A string"}
    assert schema["properties"]["n"] == {"type": "number", "description": "A number"}
    assert schema["properties"]["b"] == {"type": "boolean", "description": "A bool"}
    assert schema["properties"]["e"] == {"type": "string", "description": "Pick one",
                                         "enum": ["x", "y"]}
    # No $refs anywhere in the envelope.
    assert "$ref" not in str(schema)


def test_build_extraction_schema_empty_falls_back():
    assert build_extraction_schema([]) is None
