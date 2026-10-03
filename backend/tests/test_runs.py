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


def test_feedback_edit_overwrites_latest(client, monkeypatch):
    aid = _setup(client, monkeypatch)
    rid = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": [aid], "model": "test-model"}).json()["id"]

    assert client.post(f"/api/v1/runs/{rid}/feedback", json={
        "agent_name": "A", "attribute_name": "email",
        "rating": "up", "remarks": "first"}).json() == {"ok": True}
    assert client.post(f"/api/v1/runs/{rid}/feedback", json={
        "agent_name": "A", "attribute_name": "email",
        "rating": "down", "remarks": "second"}).json() == {"ok": True}

    logs = client.get("/api/v1/logs").json()
    assert logs[0]["feedback"] == {"A": {"email": {"rating": "down", "remarks": "second"}}}

    got = client.get(f"/api/v1/runs/{rid}/feedback").json()
    assert [(f["rating"], f["remarks"]) for f in got] == [("up", "first"), ("down", "second")]


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
        assert call["response_format"] == {
            "type": "json_schema",
            "json_schema": {"name": "meeting_extraction", "strict": True,
                            "schema": call["response_format"]["json_schema"]["schema"]},
        }
        schema = call["response_format"]["json_schema"]["schema"]
        assert schema["required"] == ["mood"]
        assert schema["additionalProperties"] is False
        props = schema["properties"]
        assert props["mood"] == {
            "type": "object", "description": "Caller mood",
            "properties": {
                "value": {"anyOf": [{"type": "string", "enum": ["good", "bad"]},
                                    {"type": "null"}],
                          "description": "Extracted value for mood"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "confidence_type": {"type": "string",
                                    "enum": ["quoted", "inferred", "normalized", "calculated", "not_found"]},
                "evidence": {"type": ["string", "null"]},
            },
            "required": ["value", "confidence", "confidence_type", "evidence"],
            "additionalProperties": False,
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
         "group": "",
         "enum_values": ["good", "bad"], "object_properties": [],
         "array_items": {"kind": "string", "properties": []},
         "wrap_result": True}]
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
    assert schema["required"] == ["s", "n", "b", "e"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["s"]["properties"]["value"] == {
        "type": ["string", "null"], "description": "Extracted value for s"}
    assert schema["properties"]["n"]["properties"]["value"] == {
        "type": ["number", "null"], "description": "Extracted value for n"}
    assert schema["properties"]["b"]["properties"]["value"] == {
        "type": ["boolean", "null"], "description": "Extracted value for b"}
    assert schema["properties"]["e"]["properties"]["value"] == {
        "anyOf": [{"type": "string", "enum": ["x", "y"]}, {"type": "null"}],
        "description": "Extracted value for e"}
    for key in ("s", "n", "b"):
        assert "enum" not in schema["properties"][key]["properties"]["value"]
    # Empty-enum edge falls back to plain string value (no enum key).
    empty = build_extraction_schema([_attr("e2", "enum", "Empty", [])])
    assert empty["properties"]["e2"]["properties"]["value"] == {
        "type": ["string", "null"], "description": "Extracted value for e2"}
    assert "enum" not in empty["properties"]["e2"]["properties"]["value"]
    assert empty["required"] == ["e2"]
    for prop in schema["properties"].values():
        assert prop["required"] == ["value", "confidence", "confidence_type", "evidence"]
        assert prop["properties"]["confidence"] == {"type": "number", "minimum": 0, "maximum": 1}
        assert prop["properties"]["confidence_type"] == {
            "type": "string", "enum": ["quoted", "inferred", "normalized", "calculated", "not_found"]}
        assert prop["properties"]["evidence"] == {"type": ["string", "null"]}
        assert prop["additionalProperties"] is False
    # No $refs anywhere in the envelope.
    assert "$ref" not in str(schema)


def test_build_extraction_schema_user_example():
    """Spec example: client_name/age/is_nri/client_type/annual_income validates."""
    from app.db.models import Attribute

    def _attr(name, type_, desc="", enum_values=None):
        return Attribute(name=name, type=type_, description=desc,
                         enum_values=enum_values or [])

    schema = build_extraction_schema([
        _attr("client_name", "string", "Client name"),
        _attr("age", "number", "Age"),
        _attr("is_nri", "boolean", "NRI flag"),
        _attr("client_type", "enum", "Client segment",
              ["HNI", "UHNI", "mass_affluent", "retail"]),
        _attr("annual_income", "number", "Annual income"),
    ])
    assert schema["required"] == ["client_name", "age", "is_nri",
                                 "client_type", "annual_income"]
    assert schema["properties"]["client_name"]["properties"]["value"]["type"] == ["string", "null"]
    assert schema["properties"]["age"]["properties"]["value"]["type"] == ["number", "null"]
    assert schema["properties"]["is_nri"]["properties"]["value"]["type"] == ["boolean", "null"]
    assert schema["properties"]["client_type"]["properties"]["value"] == {
        "anyOf": [{"type": "string", "enum": ["HNI", "UHNI", "mass_affluent", "retail"]},
                  {"type": "null"}],
        "description": "Extracted value for client_type"}
    assert schema["properties"]["annual_income"]["properties"]["value"]["type"] == ["number", "null"]


def test_result_contract_text():
    assert runs_router.RESULT_CONTRACT == (
        'Return a JSON object keyed by attribute name. Each value is an object with "value" '
        '(the extracted value), "confidence" (0-1), "confidence_type" (quoted|inferred|normalized), '
        '"evidence" (exact quote from the input). If an attribute is not found in the input, '
        'omit it from the response — never return null. '
        'quoted = value stated word-for-word (evidence is the exact quote); '
        'inferred = value concluded from the input but not stated verbatim '
        '(evidence is the supporting passage); normalized = value standardized from a stated form '
        'such as phone digits, date formats, or casing (evidence is the original stated form).\n\n'
        '| Confidence Type | Meaning |\n'
        '|---|---|\n'
        '| `quoted` | Value is explicitly stated in the transcript |\n'
        '| `normalized` | Value is explicitly stated but transformed into your canonical representation |\n'
        '| `inferred` | Value was not directly stated; model derived it from evidence |\n'
        '| `not_found` | No sufficient evidence exists |\n'
        '| `calculated` | Mentioned as pieces of info, but model performed calculations to arrive |'
    )
    assert "…" not in runs_router.RESULT_CONTRACT
    assert "(quoted|inferred|normalized)" in runs_router.RESULT_CONTRACT
    for term in ("quoted", "inferred", "normalized"):
        assert term in runs_router.RESULT_CONTRACT
    assert "quoted = value stated word-for-word" in runs_router.RESULT_CONTRACT
    assert "inferred = value concluded from the input" in runs_router.RESULT_CONTRACT
    assert "normalized = value standardized from a stated form" in runs_router.RESULT_CONTRACT
    # Confidence-type table (exact markdown) rides in the contract so ALL
    # agents get it at prompt build time without touching stored instructions.
    for row in (
        "| `quoted` | Value is explicitly stated in the transcript |",
        "| `normalized` | Value is explicitly stated but transformed into your canonical representation |",
        "| `inferred` | Value was not directly stated; model derived it from evidence |",
        "| `not_found` | No sufficient evidence exists |",
        "| `calculated` | Mentioned as pieces of info, but model performed calculations to arrive |",
    ):
        assert row in runs_router.RESULT_CONTRACT


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


def _wrap(name, value_schema, description):
    return {
        "type": "object", "description": description,
        "properties": {
            "value": value_schema,
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "confidence_type": {"type": "string",
                                "enum": ["quoted", "inferred", "normalized", "calculated", "not_found"]},
            "evidence": {"type": ["string", "null"]},
        },
        "required": ["value", "confidence", "confidence_type", "evidence"],
        "additionalProperties": False,
    }


def test_build_extraction_schema_array_shape():
    """Spec example: emails array emits items-string value schema."""
    from app.db.models import Attribute

    schema = build_extraction_schema([
        Attribute(name="emails", type="array",
                  description="Email addresses mentioned in the meeting."),
    ])
    assert schema["required"] == ["emails"]
    assert schema["properties"]["emails"] == _wrap(
        "emails",
        {"type": ["array", "null"], "items": {"type": "string"},
         "description": "Extracted value for emails"},
        "Email addresses mentioned in the meeting.")


def test_build_extraction_schema_object_shape():
    """Spec example: asset_allocation object with ordered number sub-fields."""
    from app.db.models import Attribute

    schema = build_extraction_schema([
        Attribute(name="asset_allocation", type="object",
                  description="Asset allocation",
                  object_properties=[
                      {"name": "equity", "type": "number", "null_allowed": True},
                      {"name": "debt", "type": "number", "null_allowed": True},
                      {"name": "real_estate", "type": "number", "null_allowed": True},
                      {"name": "gold", "type": "number", "null_allowed": True},
                  ]),
    ])
    assert schema["required"] == ["asset_allocation"]
    assert schema["properties"]["asset_allocation"] == _wrap(
        "asset_allocation",
        {"type": ["object", "null"],
         "properties": {"equity": {"type": ["number", "null"]},
                        "debt": {"type": ["number", "null"]},
                        "real_estate": {"type": ["number", "null"]},
                        "gold": {"type": ["number", "null"]}},
         "required": ["equity", "debt", "real_estate", "gold"],
         "additionalProperties": False,
         "description": "Extracted value for asset_allocation"},
        "Asset allocation")
    assert "$ref" not in str(schema)


def test_build_extraction_schema_object_subtype_mapping():
    """Sub-field null_allowed toggles the null union per type."""
    from app.db.models import Attribute
    from app.modules.runs.router import _sub_schema

    assert _sub_schema("string", True) == {"type": ["string", "null"]}
    assert _sub_schema("string", False) == {"type": "string"}
    assert _sub_schema("number", True) == {"type": ["number", "null"]}
    assert _sub_schema("number", False) == {"type": "number"}
    assert _sub_schema("boolean", True) == {"type": ["boolean", "null"]}
    assert _sub_schema("boolean", False) == {"type": "boolean"}
    assert _sub_schema("array", True) == {"type": ["array", "null"],
                                          "items": {"type": "string"}}
    assert _sub_schema("array", False) == {"type": "array",
                                           "items": {"type": "string"}}

    schema = build_extraction_schema([
        Attribute(name="profile", type="object", description="Profile",
                  object_properties=[
                      {"name": "nickname", "type": "string", "null_allowed": False},
                      {"name": "tags", "type": "array", "null_allowed": True},
                      {"name": "vip", "type": "boolean", "null_allowed": False},
                  ]),
    ])
    value = schema["properties"]["profile"]["properties"]["value"]
    assert value["properties"] == {"nickname": {"type": "string"},
                                   "tags": {"type": ["array", "null"],
                                            "items": {"type": "string"}},
                                   "vip": {"type": "boolean"}}
    assert value["required"] == ["nickname", "tags", "vip"]
    assert value["additionalProperties"] is False


def test_nullable_enum_subfields_use_anyof():
    """Anthropic rejects {"type": [...], "enum": [...]} — nullable enums use anyOf."""
    from app.db.models import Attribute
    from app.modules.runs.router import _sub_schema

    assert _sub_schema("string", True, ["India", "Foreign"], "Seg") == {
        "anyOf": [{"type": "string", "enum": ["India", "Foreign"]}, {"type": "null"}],
        "description": "Seg",
    }
    assert _sub_schema("string", False, ["India", "Foreign"], "Seg") == {
        "type": "string", "enum": ["India", "Foreign"], "description": "Seg",
    }
    assert _sub_schema("string", True, ["a", "b"]) == {
        "anyOf": [{"type": "string", "enum": ["a", "b"]}, {"type": "null"}],
    }
    assert _sub_schema("string", True, None, "Hi") == {
        "type": ["string", "null"], "description": "Hi"}

    schema = build_extraction_schema([
        Attribute(name="kyc", type="object", description="KYC",
                  object_properties=[
                      {"name": "seg", "type": "string", "null_allowed": True,
                       "enum": ["India", "Foreign"], "description": "Segment"},
                      {"name": "band", "type": "string", "null_allowed": False,
                       "enum": ["HNI", "retail"], "description": "Band"},
                  ]),
    ])
    props = schema["properties"]["kyc"]["properties"]["value"]["properties"]
    assert props["seg"] == {
        "anyOf": [{"type": "string", "enum": ["India", "Foreign"]}, {"type": "null"}],
        "description": "Segment",
    }
    assert props["band"] == {
        "type": "string", "enum": ["HNI", "retail"], "description": "Band",
    }

    schema2 = build_extraction_schema([
        Attribute(name="holdings", type="array", description="Holdings",
                  array_items={"kind": "object", "properties": [
                      {"name": "description", "type": "string", "null_allowed": True,
                       "enum": ["India", "Foreign", "Overall"], "description": "Which"},
                      {"name": "value", "type": "number", "null_allowed": True},
                  ]}),
    ])
    items_props = schema2["properties"]["holdings"]["properties"]["value"]["items"]["properties"]
    assert items_props["description"] == {
        "anyOf": [{"type": "string", "enum": ["India", "Foreign", "Overall"]},
                  {"type": "null"}],
        "description": "Which",
    }
    assert items_props["value"] == {"type": ["number", "null"]}

    schema3 = build_extraction_schema([
        Attribute(name="mood", type="enum", description="Mood",
                  enum_values=["good", "bad"]),
    ])
    assert schema3["properties"]["mood"]["properties"]["value"] == {
        "anyOf": [{"type": "string", "enum": ["good", "bad"]}, {"type": "null"}],
        "description": "Extracted value for mood",
    }

    def _walk(o):
        if isinstance(o, dict):
            t = o.get("type")
            if isinstance(t, list) and "enum" in o:
                raise AssertionError(f"type-array + enum combo still present: {o}")
            for v in o.values():
                _walk(v)
        elif isinstance(o, list):
            for v in o:
                _walk(v)

    _walk(schema)
    _walk(schema2)
    _walk(schema3)


def test_run_agents_execute_concurrently(client, monkeypatch):
    """Two agents overlap in time; outputs order still matches agent order."""
    import asyncio
    import time

    import app.modules.runs.router as rr

    aid1 = client.post(
        "/api/v1/agents",
        json={"name": "C1", "system_instruction": "sys-first"}).json()["id"]
    aid2 = client.post(
        "/api/v1/agents",
        json={"name": "C2", "system_instruction": "sys-second"}).json()["id"]
    client.post("/api/v1/attributes", json={"agent_ids": [aid1], "name": "email"})
    client.post("/api/v1/attributes", json={"agent_ids": [aid2], "name": "email"})

    starts: dict = {}
    ends: dict = {}

    async def _sleepy(payload):
        text = payload["messages"][0]["content"]
        key = "first" if "sys-first" in text else "second"
        starts[key] = time.monotonic()
        await asyncio.sleep(0.3 if key == "first" else 0.1)
        ends[key] = time.monotonic()
        return ({"ok": key}, {"prompt_tokens": 1, "completion_tokens": 1,
                              "total_tokens": 2})

    async def _no_price(model):
        return (None, None)

    monkeypatch.setattr(rr, "complete_json_payload", _sleepy)
    monkeypatch.setattr(rr, "get_model_pricing", _no_price)

    body = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [aid1, aid2], "model": "m"}).json()

    assert set(starts) == {"first", "second"} and set(ends) == {"first", "second"}
    assert max(starts.values()) < min(ends.values())
    assert ends["second"] < ends["first"]
    assert list(body["outputs"].keys()) == [aid1, aid2]
    assert list(body["usage"]["per_agent"].keys()) == [aid1, aid2]
    assert list(body["requests"].keys()) == [aid1, aid2]


def test_run_group_id_round_trip(client, monkeypatch):
    aid = _setup(client, monkeypatch)
    gid = "c" * 32
    body = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": [aid], "model": "test-model",
        "run_group_id": gid}).json()
    assert body["run_group_id"] == gid
    assert body["log"]["run_group_id"] == gid
    assert body["log_id"] == body["log"]["id"]

    logs = client.get("/api/v1/logs").json()
    assert len(logs) == 1
    assert logs[0]["id"] == body["log_id"]
    assert logs[0]["run_group_id"] == gid

    # Non-str coerces to ""; bad shapes -> 422.
    bad = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "x",
        "agent_ids": [aid], "model": "test-model",
        "run_group_id": "not-hex"})
    assert bad.status_code == 422
    assert client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "x",
        "agent_ids": [aid], "model": "test-model",
        "run_group_id": "ABC"}).status_code == 422


def test_run_response_log_id_matches_newest_log(client, monkeypatch):
    aid = _setup(client, monkeypatch)
    first = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": [aid], "model": "test-model"}).json()
    second = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": [aid], "model": "test-model"}).json()
    assert first["log_id"] != second["log_id"]
    logs = client.get("/api/v1/logs").json()
    assert len(logs) == 2
    # Logs are newest-first, so the second run's log is on top.
    assert logs[0]["id"] == second["log_id"]
    assert second["log"]["id"] == second["log_id"]


def test_feedback_clear_removes_key(client, monkeypatch):
    aid = _setup(client, monkeypatch)
    rid = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": [aid], "model": "test-model"}).json()["id"]

    assert client.post(f"/api/v1/runs/{rid}/feedback", json={
        "agent_name": "A", "attribute_name": "email",
        "rating": "up", "remarks": "keep me"}).json() == {"ok": True}
    # remarks None keeps existing text.
    assert client.post(f"/api/v1/runs/{rid}/feedback", json={
        "agent_name": "A", "attribute_name": "email",
        "rating": "down"}).json() == {"ok": True}
    logs = client.get("/api/v1/logs").json()
    assert logs[0]["feedback"] == {"A": {"email": {"rating": "down", "remarks": "keep me"}}}

    # Clear removes the cell (and the agent key when empty), history keeps "clear".
    assert client.post(f"/api/v1/runs/{rid}/feedback", json={
        "agent_name": "A", "attribute_name": "email", "rating": ""}).json() == {"ok": True}
    logs = client.get("/api/v1/logs").json()
    assert logs[0]["feedback"] == {}
    got = client.get(f"/api/v1/runs/{rid}/feedback").json()
    assert [(f["rating"], f["remarks"]) for f in got] == [
        ("up", "keep me"), ("down", "keep me"), ("clear", "keep me")]

    # Invalid ratings still 422.
    assert client.post(f"/api/v1/runs/{rid}/feedback",
                       json={"rating": "meh"}).status_code == 422


def test_feedback_batch_and_only_unrated(client, monkeypatch):
    aid = _setup(client, monkeypatch)
    mk = lambda: client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": [aid], "model": "test-model"}).json()["id"]
    r1, r2 = mk(), mk()

    body = client.post("/api/v1/runs/feedback-batch", json={
        "items": [
            {"run_id": r1, "agent_name": "A", "attribute_name": "email",
             "rating": "up", "remarks": "one"},
            {"run_id": r2, "agent_name": "A", "attribute_name": "email",
             "rating": "down", "remarks": "two"},
        ]}).json()
    assert body == {"ok": True, "applied": 2, "skipped": 0}
    logs = {l["run_id"]: l for l in client.get("/api/v1/logs").json()}
    assert logs[r1]["feedback"] == {"A": {"email": {"rating": "up", "remarks": "one"}}}
    assert logs[r2]["feedback"] == {"A": {"email": {"rating": "down", "remarks": "two"}}}

    # only_unrated skips already-rated cells.
    body = client.post("/api/v1/runs/feedback-batch", json={
        "items": [
            {"run_id": r1, "agent_name": "A", "attribute_name": "email",
             "rating": "down", "remarks": "changed"},
            {"run_id": r2, "agent_name": "A", "attribute_name": "other",
             "rating": "up", "remarks": "new"},
        ], "only_unrated": True}).json()
    assert body == {"ok": True, "applied": 1, "skipped": 1}
    logs = {l["run_id"]: l for l in client.get("/api/v1/logs").json()}
    assert logs[r1]["feedback"]["A"]["email"]["rating"] == "up"
    assert logs[r2]["feedback"]["A"]["other"] == {"rating": "up", "remarks": "new"}

    # Unknown run -> 404 and nothing applied.
    before = client.get("/api/v1/logs").json()
    resp = client.post("/api/v1/runs/feedback-batch", json={
        "items": [
            {"run_id": r1, "agent_name": "A", "attribute_name": "email",
             "rating": "down", "remarks": "x"},
            {"run_id": "missing", "agent_name": "A", "attribute_name": "email",
             "rating": "up"},
        ]})
    assert resp.status_code == 404
    after = client.get("/api/v1/logs").json()
    assert before == after


def _check(client, aid, model, effort="", input_data="mail me at a@b.in"):
    body = {"input_type": "mail", "input_data": input_data,
            "agent_ids": [aid],
            "models": [{"model": model, "reasoning_effort": effort}]}
    resp = client.post("/api/v1/runs/check-existing", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_check_existing_no_match_empty(client):
    aid = client.post("/api/v1/agents", json={"name": "A"}).json()["id"]
    client.post("/api/v1/attributes", json={"agent_ids": [aid], "name": "email"})
    data = _check(client, aid, "test-model")
    assert len(data["slots"]) == 1
    assert data["slots"][0]["model"] == "test-model"
    assert data["slots"][0]["reasoning_effort"] == ""
    assert data["slots"][0]["agents"] == []


def test_check_existing_match_after_run(client, monkeypatch):
    aid = _setup(client, monkeypatch)
    run = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": [aid], "model": "test-model"}).json()
    log_id = run["log_id"]

    async def _boom(payload):
        raise AssertionError("check-existing must not call OpenRouter")

    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)
    before = client.get("/api/v1/logs").json()
    data = _check(client, aid, "test-model")
    assert len(data["slots"]) == 1
    assert data["slots"][0]["model"] == "test-model"
    assert data["slots"][0]["reasoning_effort"] == ""
    assert len(data["slots"][0]["agents"]) == 1
    entry = data["slots"][0]["agents"][0]
    assert entry["agent_id"] == aid
    assert entry["log_id"] == log_id
    assert entry["created_at"]
    # Nothing written.
    assert client.get("/api/v1/logs").json() == before


def test_check_existing_different_effort_no_match(client, monkeypatch):
    aid = _setup(client, monkeypatch)
    client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": [aid], "model": "test-model"}).json()
    assert _check(client, aid, "test-model", effort="high")["slots"][0]["agents"] == []
    assert _check(client, aid, "other-model")["slots"][0]["agents"] == []
    # Same effort still matches.
    assert len(_check(client, aid, "test-model")["slots"][0]["agents"]) == 1


def test_check_existing_changed_prompt_no_match(client, monkeypatch):
    aid = _setup(client, monkeypatch)
    client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": [aid], "model": "test-model"}).json()
    assert len(_check(client, aid, "test-model")["slots"][0]["agents"]) == 1
    client.patch(f"/api/v1/agents/{aid}", json={"system_instruction": "changed"})
    assert _check(client, aid, "test-model")["slots"][0]["agents"] == []


def test_check_existing_all_errored_no_match(client, monkeypatch):
    aid = client.post("/api/v1/agents", json={"name": "A"}).json()["id"]
    client.post("/api/v1/attributes", json={"agent_ids": [aid], "name": "email"})

    async def _boom(payload):
        raise RuntimeError("provider down")

    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)
    body = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [aid], "model": "m"}).json()
    assert "_error" in body["outputs"][aid]

    async def _must_not_run(payload):
        raise AssertionError("check-existing must not call OpenRouter")

    monkeypatch.setattr(runs_router, "complete_json_payload", _must_not_run)
    assert _check(client, aid, "m", input_data="hello")["slots"][0]["agents"] == []


def test_reuse_creates_new_row(client, monkeypatch):
    aid = _setup(client, monkeypatch)
    run = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": [aid], "model": "test-model"}).json()
    log_id = run["log_id"]

    # Normal rows carry "" / null reuse markers.
    logs = client.get("/api/v1/logs").json()
    assert len(logs) == 1
    assert logs[0]["reused_from_log_id"] == ""
    assert logs[0]["reused_from_created_at"] is None

    gid = "d" * 32

    async def _boom(payload):
        raise AssertionError("reuse must not call OpenRouter")

    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)
    reused = client.post("/api/v1/runs/reuse", json={
        "log_id": log_id, "run_group_id": gid}).json()
    assert reused["log_id"] != log_id
    assert reused["id"] != run["id"]
    assert reused["outputs"] == run["outputs"]
    assert reused["usage"] == run["usage"]
    assert reused["requests"] == run["requests"]
    assert reused["run_group_id"] == gid
    assert reused["log"]["id"] == reused["log_id"]
    assert reused["log"]["run_group_id"] == gid
    assert reused["log"]["reused_from_log_id"] == log_id
    assert reused["log"]["feedback"] == {}
    assert reused["log"]["reused_from_created_at"] is not None

    # Copied snapshots match the source.
    assert reused["log"]["input_type"] == run["log"]["input_type"]
    assert reused["log"]["input_data"] == run["log"]["input_data"]
    assert reused["log"]["model"] == run["log"]["model"]

    logs = client.get("/api/v1/logs").json()
    assert len(logs) == 2
    by_id = {l["id"]: l for l in logs}
    assert by_id[log_id]["reused_from_log_id"] == ""
    assert by_id[log_id]["reused_from_created_at"] is None
    assert by_id[reused["log_id"]]["reused_from_log_id"] == log_id


def test_reuse_of_reused_points_at_original(client, monkeypatch):
    aid = _setup(client, monkeypatch)
    run = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": [aid], "model": "test-model"}).json()
    first = client.post("/api/v1/runs/reuse", json={
        "log_id": run["log_id"], "run_group_id": "e" * 32}).json()
    second = client.post("/api/v1/runs/reuse", json={
        "log_id": first["log_id"], "run_group_id": "f" * 32}).json()
    assert second["log_id"] != first["log_id"]
    assert second["log"]["reused_from_log_id"] == run["log_id"]
    assert second["log"]["reused_from_created_at"] == first["log"]["reused_from_created_at"]
    assert second["run_group_id"] == "f" * 32


def test_reuse_unknown_log_404(client):
    resp = client.post("/api/v1/runs/reuse", json={
        "log_id": "missing", "run_group_id": "d" * 32})
    assert resp.status_code == 404


def test_reuse_bad_group_id_422(client, monkeypatch):
    aid = _setup(client, monkeypatch)
    log_id = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": [aid], "model": "test-model"}).json()["log_id"]
    assert client.post("/api/v1/runs/reuse", json={
        "log_id": log_id, "run_group_id": "not-hex"}).status_code == 422
    assert client.post("/api/v1/runs/reuse", json={
        "log_id": log_id, "run_group_id": "ABC"}).status_code == 422
