"""Coverage: per-agent LLM request payloads (build/post/persist).

Synthetic only — no live keys, no PII.
"""

import asyncio

import app.core.openrouter as openrouter
import app.modules.runs.router as runs_router
from app.core.openrouter import build_chat_payload
from app.db.models import RunLog


def _schema():
    return {"type": "object", "properties": {"mood": {
        "type": "string", "description": "Mood", "enum": ["good", "bad"]}},
        "required": [], "additionalProperties": False}


def test_build_chat_payload_content():
    schema = _schema()
    payload = build_chat_payload(model="m", system="sys", user="hello", json_schema=schema)
    # Exact body shape: nothing but model / response_format / messages.
    assert set(payload) == {"model", "messages", "response_format"}
    assert payload["model"] == "m"
    assert payload["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
    ]
    assert payload["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "extraction", "strict": False, "schema": schema}}
    # No secrets ride along in the persisted body.
    assert "Bearer" not in str(payload)
    assert "api_key" not in str(payload).lower()


def test_build_chat_payload_no_schema_falls_back():
    payload = build_chat_payload(model="m", system="s", user="u")
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"][0] == {"role": "system", "content": "s"}


def test_complete_json_wrapper_posts_built_payload(monkeypatch):
    from app.core.config import settings
    import json as _json

    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    seen: dict = {}

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": _json.dumps({"a": 1})}}]}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            seen["post_json"] = json
            return _FakeResp()

    monkeypatch.setattr(openrouter.httpx, "AsyncClient", _FakeClient)
    parsed, _ = asyncio.run(
        openrouter.complete_json(model="m", system="s", user="u", json_schema=_schema()))
    assert parsed == {"a": 1}
    assert seen["post_json"] == build_chat_payload(
        model="m", system="s", user="u", json_schema=_schema())


def _make_agent(client, system_instruction=None):
    body = {"name": "R"}
    if system_instruction is not None:
        body["system_instruction"] = system_instruction
    aid = client.post("/api/v1/agents", json=body).json()["id"]
    client.post("/api/v1/attributes", json={"agent_ids": [aid], "name": "mood"})
    return aid


def test_requests_persisted_on_success(client, monkeypatch):
    aid = _make_agent(client, system_instruction="be terse")

    async def _fake(payload):
        return ({"mood": {"value": "x", "confidence": 1.0,
                           "confidence_type": "quoted", "evidence": "e"}},
                {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5})

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(runs_router, "complete_json_payload", _fake)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)
    body = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello world",
        "agent_ids": [aid], "model": "test-model"}).json()

    req = body["requests"][aid]
    assert req["model"] == "test-model"
    assert req["messages"][0]["role"] == "system"
    system_text = req["messages"][0]["content"]
    assert system_text.startswith("be terse")
    assert "- mood (string): " in system_text
    assert runs_router.RESULT_CONTRACT in system_text
    assert req["messages"][1] == {"role": "user", "content": "hello world"}
    assert req["response_format"]["type"] == "json_schema"
    assert req["response_format"]["json_schema"]["schema"]["properties"]["mood"] == {
        "type": "object", "description": "mood",
        "properties": {
            "value": {"description": "Extracted value for mood"},
            "confidence": {"type": "number"},
            "confidence_type": {"type": "string", "enum": ["quoted", "inferred", "normalized"],
                                "description": "How the value was obtained: quoted, inferred, or normalized."},
            "evidence": {"type": "string"},
        },
        "required": ["confidence"], "additionalProperties": False,
    }
    assert set(req) == {"model", "messages", "response_format"}

    logs = client.get("/api/v1/logs").json()
    assert logs[0]["requests"] == body["requests"]


def test_requests_persisted_on_error_path(client, monkeypatch):
    aid = _make_agent(client)

    async def _boom(payload):
        raise RuntimeError("provider down")

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)
    body = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [aid], "model": "m"}).json()

    assert "_error" in body["outputs"][aid]
    # The attempted request survives even though the call failed.
    req = body["requests"][aid]
    assert req["model"] == "m"
    system_text = req["messages"][0]["content"]
    assert system_text.startswith("Extract structured data.")
    assert "- mood (string): " in system_text
    assert runs_router.RESULT_CONTRACT in system_text
    assert req["messages"][1] == {"role": "user", "content": "hello"}

    logs = client.get("/api/v1/logs").json()
    assert logs[0]["outputs"][aid]["_error"] == "provider down"
    assert logs[0]["requests"] == body["requests"]


def test_legacy_rows_default_empty_requests(client, db):
    # Old rows constructed before the column existed carry no requests kwarg.
    row = RunLog(run_id="legacy-run", input_type="mail", input_data="hi",
                 model="m", agent_snapshot={}, attribute_snapshot={},
                 outputs={}, feedback={}, usage={}, filters={})
    assert (row.requests or {}) == {}
    db.add(row)
    db.commit()

    logs = client.get("/api/v1/logs").json()
    assert len(logs) == 1
    assert logs[0]["requests"] == {}
    assert client.get(f"/api/v1/logs/{logs[0]['id']}").json()["requests"] == {}
