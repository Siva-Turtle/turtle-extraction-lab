"""Coverage: OpenRouter /api/alpha/decisions support for decision models.

Synthetic only — mocked httpx, no network, no secrets.
"""

import asyncio

import httpx
import pytest

import app.core.openrouter as openrouter
import app.modules.runs.router as runs_router
from app.core.config import settings
from app.core.openrouter import (
    build_chat_payload,
    build_decisions_payload,
    complete_decisions_payload,
    is_decision_model,
)
from app.modules.runs.router import (
    IDENTIFIER_QUESTIONS,
    _request_match_key,
    build_identifier_decisions_questions,
    decisions_answers_to_bools,
    normalize_identifier_output,
)


# --- is_decision_model --------------------------------------------------------


def test_is_decision_model_true():
    assert is_decision_model("typesafe/jev-1.13") is True
    assert is_decision_model("typesafe/jev-2") is True
    assert is_decision_model("  typesafe/jev-1.13  ") is True
    assert is_decision_model("Typesafe/JEV-9") is True
    assert is_decision_model("openai/solar-decide-9") is True
    assert is_decision_model("X/SOLAR-DECIDE-mini") is True


def test_is_decision_model_false():
    assert is_decision_model("openai/gpt-4o") is False
    assert is_decision_model("anthropic/claude-sonnet-4") is False
    assert is_decision_model("typesafe/jev-router") is True  # prefix match
    assert is_decision_model("typesafe/other") is False
    assert is_decision_model("jev-1.13") is False


def test_is_decision_model_edge_never_raises():
    for bad in ("", "   ", None, 123, 4.5, True, ["x"], {"m": 1}):
        assert is_decision_model(bad) is False


# --- build_decisions_payload --------------------------------------------------


def test_build_decisions_payload_with_provider():
    questions = build_identifier_decisions_questions()
    payload = build_decisions_payload(
        model="typesafe/jev-1.13", state={"transcript": "hi"},
        questions=questions, provider="  p1  ")
    assert set(payload) == {"model", "state", "questions", "provider"}
    assert payload["model"] == "typesafe/jev-1.13"
    assert payload["state"] == {"transcript": "hi"}
    assert payload["questions"] is questions
    assert payload["provider"] == {"order": ["p1"], "allow_fallbacks": False}
    assert "Bearer" not in str(payload)


def test_build_decisions_payload_without_provider():
    for prov in (None, "", "   "):
        payload = build_decisions_payload(
            model="m", state={"transcript": "t"},
            questions={"q": 1}, provider=prov)
        assert set(payload) == {"model", "state", "questions"}
    # Deterministic: same input -> identical payload.
    a = build_decisions_payload(model="m", state={"t": 1}, questions={"q": 2})
    b = build_decisions_payload(model="m", state={"t": 1}, questions={"q": 2})
    assert a == b


def test_build_identifier_decisions_questions_shape():
    questions = build_identifier_decisions_questions()
    assert set(questions) == {q["key"] for q in IDENTIFIER_QUESTIONS}
    assert len(questions) == 11
    for i, q in enumerate(IDENTIFIER_QUESTIONS, start=1):
        entry = questions[q["key"]]
        assert entry["type"] == "noul"
        assert entry["instructions"] == f"Q{i} ({q['key']}): {q['question']}"
        assert entry["criteria"] == {
            "true": "The CLIENT's own situation includes this, explicitly stated or clearly implied.",
            "false": "Not mentioned for the client, or only someone else's situation.",
        }


# --- complete_decisions_payload (mocked httpx) --------------------------------


def _fake_decisions_client(monkeypatch, *, status=200, body=None, seen=None):
    seen = seen if seen is not None else {}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            seen["url"] = url
            seen["headers"] = headers
            seen["json"] = json
            req = httpx.Request("POST", url)
            return httpx.Response(status, json=body, request=req)

    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_base_url",
                         "https://openrouter.ai/api/v1")
    monkeypatch.setattr(openrouter.httpx, "AsyncClient", _FakeClient)
    return seen


def _eleven_answers():
    keys = [q["key"] for q in IDENTIFIER_QUESTIONS]
    answers = {k: {"type": "noul", "noul": 0.9} for k in keys}
    return answers


def test_decisions_success_maps_bools_and_usage(monkeypatch):
    keys = [q["key"] for q in IDENTIFIER_QUESTIONS]
    answers = _eleven_answers()
    answers[keys[0]] = {"type": "noul", "noul": 0.5}  # threshold -> True
    answers[keys[1]] = {"type": "noul", "noul": 0.49}  # below -> False
    answers[keys[2]] = {"type": "choice", "choice": "yes"}  # wrong type -> False
    answers[keys[3]] = "yes"  # non-dict -> False
    answers[keys[4]] = {"type": "noul"}  # missing noul -> False
    del answers[keys[5]]  # missing key -> False
    seen = _fake_decisions_client(monkeypatch, body={
        "id": "d1", "model": "typesafe/jev-1.13", "provider": "pv",
        "answers": answers,
        "usage": {"input_tokens": 100, "output_tokens": 20, "cost": 0.0025},
    })
    payload = build_decisions_payload(
        model="typesafe/jev-1.13", state={"transcript": "t"},
        questions=build_identifier_decisions_questions())
    got_answers, usage = asyncio.run(complete_decisions_payload(payload))
    # Decisions URL derives from the chat base minus /v1; same auth as chat.
    assert seen["url"] == "https://openrouter.ai/api/alpha/decisions"
    assert seen["headers"]["Authorization"] == "Bearer test-key"
    assert seen["headers"]["Content-Type"] == "application/json"
    assert "transport" not in seen["json"]
    bools = decisions_answers_to_bools(got_answers)
    assert bools[keys[0]] is True
    assert bools[keys[1]] is False
    assert bools[keys[2]] is False
    assert bools[keys[3]] is False
    assert bools[keys[4]] is False
    assert bools[keys[5]] is False
    for k in keys[6:]:
        assert bools[k] is True
    assert usage == {"prompt_tokens": 100, "completion_tokens": 20,
                     "total_tokens": 120, "reasoning_tokens": 0,
                     "provider": "pv", "cost": 0.0025,
                     "cost_details": None,
                     "served_model": "typesafe/jev-1.13"}


def test_decisions_usage_missing_and_negative(monkeypatch):
    seen = _fake_decisions_client(monkeypatch, body={
        "id": "d2", "answers": {},
        "usage": {"input_tokens": -5, "output_tokens": "xx", "cost": -1},
    })
    _, usage = asyncio.run(complete_decisions_payload(
        {"model": "m", "state": {}, "questions": {}}))
    assert seen["url"].endswith("/api/alpha/decisions")
    assert usage["prompt_tokens"] == 0
    assert usage["completion_tokens"] == 0
    assert usage["total_tokens"] == 0
    assert usage["reasoning_tokens"] == 0
    assert usage["cost"] is None
    assert usage["cost_details"] is None
    assert usage["provider"] == ""
    assert usage["served_model"] == ""


def test_decisions_error_body_preserved(monkeypatch):
    msg = ("typesafe/jev-1.13 is a decisions model and cannot be used with "
           "the chat/completions endpoint. Use the `/api/alpha/decisions` "
           "endpoint instead.")
    _fake_decisions_client(monkeypatch, status=400, body={
        "error": {"code": 400, "message": msg}})
    with pytest.raises(RuntimeError) as ei:
        asyncio.run(complete_decisions_payload(
            {"model": "typesafe/jev-1.13", "state": {}, "questions": {}}))
    text = str(ei.value)
    assert "400" in text
    assert "decisions model" in text
    assert "/api/alpha/decisions" in text
    assert "Bearer" not in text


def test_decisions_unconfigured_raises(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "")

    async def _no_network(self, *a, **k):
        raise AssertionError("must not reach the network unconfigured")

    monkeypatch.setattr(openrouter.httpx, "AsyncClient", _no_network)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY is not set"):
        asyncio.run(complete_decisions_payload(
            {"model": "m", "state": {}, "questions": {}}))


# --- chat path spot-check (unchanged) ------------------------------------------


def test_chat_build_payload_spot_check_unchanged():
    schema = {"type": "object", "properties": {"a": {"type": "string"}},
              "required": [], "additionalProperties": False}
    payload = build_chat_payload(model="m", system="s", user="u",
                                 json_schema=schema)
    assert set(payload) == {"model", "messages", "response_format", "usage"}
    assert payload["usage"] == {"include": True}
    assert payload["messages"] == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]
    assert payload["response_format"]["json_schema"]["name"] == "meeting_extraction"


def test_identifier_bools_pass_through_normalize_unchanged():
    bools = {q["key"]: (i % 2 == 0) for i, q in enumerate(IDENTIFIER_QUESTIONS)}
    assert normalize_identifier_output(dict(bools)) == bools
    assert decisions_answers_to_bools({"_error": "x"}) == {"_error": "x"}
    for bad in (["x"], "str", None, 42):
        assert decisions_answers_to_bools(bad) is bad


# --- end-to-end via the client fixture ------------------------------------------


def _make_identifier(client):
    return client.post("/api/v1/agents", json={
        "name": "agent_identifier", "kind": "identifier"}).json()["id"]


def _make_extraction_agent(client):
    aid = client.post("/api/v1/agents", json={"name": "R"}).json()["id"]
    client.post("/api/v1/attributes", json={
        "agent_ids": [aid], "name": "mood"})
    return aid


def test_identifier_run_with_decision_model_end_to_end(client, monkeypatch):
    seen = {}

    async def _fake_decisions(payload):
        seen["payload"] = payload
        keys = [q["key"] for q in IDENTIFIER_QUESTIONS]
        answers = {k: {"type": "noul", "noul": 0.8} for k in keys}
        answers["tax"] = {"type": "noul", "noul": 0.2}
        return answers, {"prompt_tokens": 10, "completion_tokens": 5,
                         "total_tokens": 15, "reasoning_tokens": 0,
                         "provider": "pv", "cost": 0.001,
                         "cost_details": None,
                         "served_model": "typesafe/jev-1.13"}

    async def _no_chat(payload):
        raise AssertionError("chat must not be called for decision models")

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(runs_router, "complete_decisions_payload", _fake_decisions)
    monkeypatch.setattr(runs_router, "complete_json_payload", _no_chat)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)

    ident = _make_identifier(client)
    body = client.post("/api/v1/runs", json={
        "input_type": "transcription", "input_data": "client salary talk",
        "agent_ids": [ident], "model": "typesafe/jev-1.13"}).json()
    out = body["outputs"][ident]
    assert out["tax"] is False
    for q in IDENTIFIER_QUESTIONS:
        if q["key"] != "tax":
            assert out[q["key"]] is True
    req = body["requests"][ident]
    assert req["transport"] == "decisions"
    assert req["model"] == "typesafe/jev-1.13"
    assert "messages" not in req
    assert req["questions"]["has_assets"]["type"] == "noul"
    # The marker is storage-only: never POSTed.
    assert "transport" not in seen["payload"]
    assert seen["payload"]["model"] == "typesafe/jev-1.13"
    per = body["usage"]["per_agent"][ident]
    assert per["prompt_tokens"] == 10
    assert per["completion_tokens"] == 5
    assert per["reasoning_tokens"] == 0


def test_extraction_agent_with_decision_model_errors_without_http(client, monkeypatch):
    calls = {"chat": 0, "decisions": 0}

    async def _no_chat(payload):
        calls["chat"] += 1
        raise AssertionError("no chat call for decision models")

    async def _no_decisions(payload):
        calls["decisions"] += 1
        raise AssertionError("no decisions call for extraction agents")

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(runs_router, "complete_json_payload", _no_chat)
    monkeypatch.setattr(runs_router, "complete_decisions_payload", _no_decisions)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)

    aid = _make_extraction_agent(client)
    body = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [aid], "model": "typesafe/jev-1.13"}).json()
    assert body["outputs"][aid] == {
        "_error": "decision models support the identifier agent only"}
    assert calls == {"chat": 0, "decisions": 0}
    # The stored request stays byte-identical chat (no marker).
    req = body["requests"][aid]
    assert "transport" not in req
    assert "messages" in req
    per = body["usage"]["per_agent"][aid]
    assert per["prompt_tokens"] == 0
    assert per["completion_tokens"] == 0
    assert per["cost_usd"] is None


# --- reuse-match helper ---------------------------------------------------------


def test_request_match_key_decisions_equal_unequal():
    q1 = build_identifier_decisions_questions()
    q2 = build_identifier_decisions_questions()
    a = build_decisions_payload(model="m", state={"transcript": "t"},
                                questions=q1)
    b = build_decisions_payload(model="m", state={"transcript": "t"},
                                questions=q2)
    stored = dict(a, transport="decisions")  # marker never affects matching
    assert _request_match_key(stored) == _request_match_key(b)
    other_state = build_decisions_payload(
        model="m", state={"transcript": "different"}, questions=q1)
    assert _request_match_key(stored) != _request_match_key(other_state)
    other_model = build_decisions_payload(
        model="m2", state={"transcript": "t"}, questions=q1)
    assert _request_match_key(stored) != _request_match_key(other_model)
    other_q = build_decisions_payload(
        model="m", state={"transcript": "t"}, questions={"x": 1})
    assert _request_match_key(stored) != _request_match_key(other_q)
    # Chat-vs-chat still compares messages exactly as before.
    chat_a = build_chat_payload(model="m", system="s", user="u")
    chat_b = build_chat_payload(model="m", system="s", user="u")
    chat_c = build_chat_payload(model="m", system="s", user="other")
    assert _request_match_key(chat_a) == _request_match_key(chat_b)
    assert _request_match_key(chat_a) != _request_match_key(chat_c)
    # Cross-transport never matches.
    assert _request_match_key(stored) != _request_match_key(chat_a)
