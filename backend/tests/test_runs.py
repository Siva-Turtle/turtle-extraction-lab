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
        system_text = call["messages"][0]["content"]
        user_text = call["messages"][1]["content"]
        # user message is the input verbatim — no labels, no wrapping.
        assert user_text == "hello"
        # enum options ride along in the SYSTEM prompt...
        assert "- mood (enum): Caller mood [good | bad]" in system_text
        # ...alongside the fixed output contract.
        assert runs_router.RESULT_CONTRACT in system_text
        # ...and in the structured schema envelope as a value-object.
        props = call["response_format"]["json_schema"]["schema"]["properties"]
        assert props["mood"] == {
            "type": "object", "description": "Caller mood",
            "properties": {
                "value": {"description": "Extracted value for mood",
                          "enum": ["good", "bad"]},
                "confidence": {"type": "number"},
                "confidence_type": {"type": "string"},
                "evidence": {"type": "string"},
            },
            "required": ["confidence"], "additionalProperties": False,
        }
    systems = {c["messages"][0]["content"] for c in seen["calls"]}
    assert any("sys1" in s for s in systems) and any("sys2" in s for s in systems)
    # Stored requests mirror the new layout.
    for aid in (aid1, aid2):
        req = body["requests"][aid]
        assert req["messages"][1]["content"] == "hello"
        assert runs_router.RESULT_CONTRACT in req["messages"][0]["content"]

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

    def _value_object(name, description, enum=None):
        value = {"description": f"Extracted value for {name}"}
        if enum is not None:
            value["enum"] = enum
        return {
            "type": "object", "description": description,
            "properties": {
                "value": value,
                "confidence": {"type": "number"},
                "confidence_type": {"type": "string"},
                "evidence": {"type": "string"},
            },
            "required": ["confidence"], "additionalProperties": False,
        }

    schema = build_extraction_schema([
        _attr("s", "string", "A string"),
        _attr("n", "number", "A number"),
        _attr("b", "boolean", "A bool"),
        _attr("e", "enum", "Pick one", ["x", "y"]),
    ])
    assert schema["type"] == "object"
    assert schema["required"] == []
    assert schema["additionalProperties"] is False
    assert schema["properties"]["s"] == _value_object("s", "A string")
    assert schema["properties"]["n"] == _value_object("n", "A number")
    assert schema["properties"]["b"] == _value_object("b", "A bool")
    assert schema["properties"]["e"] == _value_object("e", "Pick one", ["x", "y"])
    # Enum value carries the verbatim enum array; non-enums carry no "enum" key.
    assert schema["properties"]["e"]["properties"]["value"] == {
        "description": "Extracted value for e", "enum": ["x", "y"]}
    for key in ("s", "n", "b"):
        assert "enum" not in schema["properties"][key]["properties"]["value"]
    # Empty-enum edge falls back to untyped value.
    empty = build_extraction_schema([_attr("e2", "enum", "Empty", [])])
    assert "enum" not in empty["properties"]["e2"]["properties"]["value"]
    for prop in schema["properties"].values():
        assert prop["required"] == ["confidence"]
    # No $refs anywhere in the envelope.
    assert "$ref" not in str(schema)


def test_result_contract_text():
    assert runs_router.RESULT_CONTRACT == (
        'Return a JSON object keyed by attribute name. Each value is an object with "value" '
        '(the extracted value), "confidence" (0-1), "confidence_type" (quoted|inferred|…), '
        '"evidence" (exact quote from the input). If an attribute is not found in the input, '
        'omit it from the response — never return null.'
    )


def test_system_layout_user_is_verbatim_input(client, monkeypatch):
    seen = {}

    async def _capture(payload):
        seen.setdefault("calls", []).append(payload)
        return ({"ok": 1}, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    monkeypatch.setattr(runs_router, "complete_json_payload", _capture)
    aid = client.post(
        "/api/v1/agents", json={"name": "S", "system_instruction": "be precise"}).json()["id"]
    client.post("/api/v1/attributes", json={
        "agent_ids": [aid], "name": "email", "type": "string", "description": "Contact email"})
    raw = "hello a@b.in\nsecond line"
    body = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": raw,
        "agent_ids": [aid], "model": "m"}).json()
    assert len(seen["calls"]) == 1
    system_text = seen["calls"][0]["messages"][0]["content"]
    assert system_text.startswith("be precise")
    assert "- email (string): Contact email" in system_text
    assert runs_router.RESULT_CONTRACT in system_text
    assert seen["calls"][0]["messages"][1]["content"] == raw
    assert body["requests"][aid]["messages"][1]["content"] == raw
    assert body["requests"][aid]["messages"][0]["content"] == system_text


def test_system_layout_no_attributes_fallback(client, monkeypatch):
    seen = {}

    async def _capture(payload):
        seen.setdefault("calls", []).append(payload)
        return ({"ok": 1}, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    monkeypatch.setattr(runs_router, "complete_json_payload", _capture)
    aid = client.post("/api/v1/agents", json={"name": "N"}).json()["id"]
    body = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "verbatim hi",
        "agent_ids": [aid], "model": "m"}).json()
    system_text = seen["calls"][0]["messages"][0]["content"]
    assert "- (no attributes defined)" in system_text
    assert runs_router.RESULT_CONTRACT in system_text
    assert seen["calls"][0]["messages"][1]["content"] == "verbatim hi"
    assert seen["calls"][0]["response_format"] == {"type": "json_object"}
    assert body["requests"][aid]["messages"][1]["content"] == "verbatim hi"


def test_build_extraction_schema_empty_falls_back():
    assert build_extraction_schema([]) is None
