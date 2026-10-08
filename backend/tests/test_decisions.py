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
    fireflies_url_from_task,
    normalize_identifier_output,
    split_identifier_result,
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
    assert len(questions) == len(IDENTIFIER_QUESTIONS) == 27
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


def _all_answers():
    keys = [q["key"] for q in IDENTIFIER_QUESTIONS]
    answers = {k: {"type": "noul", "noul": 0.9} for k in keys}
    return answers


def test_decisions_success_maps_bools_and_usage(monkeypatch):
    keys = [q["key"] for q in IDENTIFIER_QUESTIONS]
    answers = _all_answers()
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


# --- chunk evidence: __evidence choice questions ---------------------------------


def _chunks(n):
    return [{"n": i + 1, "text": f"turn {i + 1}"} for i in range(n)]


def test_decisions_questions_with_chunks_add_evidence_scores():
    questions = build_identifier_decisions_questions(_chunks(3))
    keys = [q["key"] for q in IDENTIFIER_QUESTIONS]
    assert len(questions) == 2 * len(keys) == 54
    for k in keys:
        assert questions[k]["type"] == "noul"
        ev = questions[f"{k}__evidence"]
        assert ev["type"] == "choice"
        assert ev["instructions"] == (
            f"Which transcript chunk (1..3) best supports a true answer to {k}?"
            " Reply with the chunk number.")
        assert ev["criteria"] == {"1": "chunk 1", "2": "chunk 2", "3": "chunk 3"}


def test_decisions_questions_without_chunks_stays_base_count():
    # Backward compat: no chunks -> only the base noul questions.
    n = len(IDENTIFIER_QUESTIONS)
    assert n == 27
    assert len(build_identifier_decisions_questions()) == n
    assert len(build_identifier_decisions_questions([])) == n


def test_split_decisions_evidence_round_clamp_ignore_when_false():
    # Old score shape stays accepted as a fallback (round/clamp).
    keys = [q["key"] for q in IDENTIFIER_QUESTIONS]
    answers = {k: {"type": "noul", "noul": 0.9} for k in keys}
    answers[f"{keys[0]}__evidence"] = {"type": "score", "score": 0}  # -> chunk 1
    answers[f"{keys[1]}__evidence"] = {"type": "score", "score": 1.6}  # round 2 -> chunk 3
    answers[f"{keys[2]}__evidence"] = {"type": "score", "score": 99}  # clamp -> chunk 3
    answers[f"{keys[3]}__evidence"] = {"type": "score", "score": -4}  # clamp -> chunk 1
    answers[f"{keys[4]}__evidence"] = {"type": "choice", "choice": "chunk 2"}  # non-numeric -> None
    answers[f"{keys[5]}__evidence"] = {"type": "score"}  # missing score
    # keys[6] has no __evidence entry at all.
    answers[keys[7]] = {"type": "noul", "noul": 0.1}  # False ...
    answers[f"{keys[7]}__evidence"] = {"type": "score", "score": 2}  # ... so ignored
    bools, evidence, probs = split_identifier_result(answers, "decisions", 3)
    assert bools[keys[0]] is True and evidence[keys[0]] == 1
    assert evidence[keys[1]] == 3
    assert evidence[keys[2]] == 3
    assert evidence[keys[3]] == 1
    assert evidence[keys[4]] is None
    assert evidence[keys[5]] is None
    assert evidence[keys[6]] is None
    assert bools[keys[7]] is False and evidence[keys[7]] is None
    # Probs carry the noul floats; non-numeric/missing become None.
    assert probs[keys[0]] == 0.9
    assert probs[keys[7]] == 0.1
    answers2 = dict(answers)
    answers2[keys[8]] = {"type": "noul"}  # missing noul
    answers2[keys[9]] = "yes"  # non-dict
    _, _, probs2 = split_identifier_result(answers2, "decisions", 3)
    assert probs2[keys[8]] is None
    assert probs2[keys[9]] is None


def test_split_decisions_bool_score_never_numeric():
    keys = [q["key"] for q in IDENTIFIER_QUESTIONS]
    answers = {k: {"type": "noul", "noul": 0.9} for k in keys}
    answers[f"{keys[0]}__evidence"] = {"type": "score", "score": True}
    _, evidence, _ = split_identifier_result(answers, "decisions", 3)
    assert evidence[keys[0]] is None


def test_split_decisions_evidence_choice_shape():
    keys = [q["key"] for q in IDENTIFIER_QUESTIONS]
    answers = {k: {"type": "noul", "noul": 0.9} for k in keys}
    answers[f"{keys[0]}__evidence"] = {"type": "choice", "choice": "2"}  # valid
    answers[f"{keys[1]}__evidence"] = {"type": "choice", "choice": "9"}  # out-of-range
    answers[f"{keys[2]}__evidence"] = {"type": "choice", "choice": "0"}  # out-of-range
    answers[f"{keys[3]}__evidence"] = {"type": "choice", "choice": "chunk 2"}  # non-numeric
    answers[f"{keys[4]}__evidence"] = {"type": "choice"}  # missing choice
    answers[f"{keys[5]}__evidence"] = {"type": "choice", "choice": True}  # bool never numeric
    # keys[6] has no __evidence entry at all.
    answers[keys[7]] = {"type": "noul", "noul": 0.1}  # False ...
    answers[f"{keys[7]}__evidence"] = {"type": "choice", "choice": "2"}  # ... so ignored
    bools, evidence, _ = split_identifier_result(answers, "decisions", 3)
    assert evidence[keys[0]] == 2
    assert evidence[keys[1]] is None
    assert evidence[keys[2]] is None
    assert evidence[keys[3]] is None
    assert evidence[keys[4]] is None
    assert evidence[keys[5]] is None
    assert evidence[keys[6]] is None
    assert bools[keys[7]] is False and evidence[keys[7]] is None


def test_split_chat_value_evidence_objects_and_legacy():
    keys = [q["key"] for q in IDENTIFIER_QUESTIONS]
    parsed = {k: {"value": True, "evidence": 2} for k in keys}
    parsed[keys[1]] = {"value": False, "evidence": 2}  # chat: range-valid only
    parsed[keys[2]] = {"value": True, "evidence": 9}  # out of range -> None
    parsed[keys[3]] = {"value": True, "evidence": None}
    parsed[keys[4]] = True  # legacy bool
    parsed[keys[5]] = "TrUe"  # legacy string
    bools, evidence, probs = split_identifier_result(parsed, "chat", 4)
    assert bools[keys[0]] is True and evidence[keys[0]] == 2
    assert bools[keys[1]] is False and evidence[keys[1]] == 2
    assert evidence[keys[2]] is None
    assert evidence[keys[3]] is None
    assert bools[keys[4]] is True and evidence[keys[4]] is None
    assert bools[keys[5]] is True
    assert all(v is None for v in probs.values())


def test_split_never_raises_unknown_shapes():
    for bad in (["x"], "str", None, 42):
        assert split_identifier_result(bad, "chat", 3) == (bad, {}, {})
        assert split_identifier_result(bad, "decisions", 3) == (bad, {}, {})
    err = {"_error": "boom"}
    assert split_identifier_result(err, "chat", 3) == (err, {}, {})
    bools, evidence, probs = split_identifier_result({"weird": 1}, "chat", 3)
    assert set(bools) == {q["key"] for q in IDENTIFIER_QUESTIONS}
    assert all(v is False for v in bools.values())
    assert all(v is None for v in evidence.values())
    assert all(v is None for v in probs.values())


def test_normalize_chat_value_evidence_objects():
    parsed = {"has_assets": {"value": True, "evidence": 3},
              "insurance": {"value": "TrUe", "evidence": None},
              "tax": {"value": "FALSE", "evidence": 1},
              "income": {"value": 1, "evidence": 1}}
    out = normalize_identifier_output(parsed)
    assert out["has_assets"] is True
    assert out["insurance"] is True
    assert out["tax"] is False
    assert out["income"] is False


def test_fireflies_url_helper_edge_cases():
    assert fireflies_url_from_task(
        {"transcriptUrl": "https://fireflies.ai/abc"}) == "https://fireflies.ai/abc"
    assert fireflies_url_from_task({"transcriptUrl": "  http://x  "}) == "http://x"
    for bad in ({}, {"transcriptUrl": ""}, {"transcriptUrl": None},
                {"transcriptUrl": 123}, {"transcriptUrl": "/relative/path"},
                {"transcriptUrl": "ftp://x"}, None, "str", ["x"]):
        assert fireflies_url_from_task(bad) == ""


def test_chat_identifier_request_uses_numbered_chunks(client, monkeypatch):
    import app.modules.runs.router as rr

    seen = {}

    async def _capture(payload):
        seen["payload"] = payload
        return ({q["key"]: False for q in IDENTIFIER_QUESTIONS},
                {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(rr, "complete_json_payload", _capture)
    monkeypatch.setattr(rr, "get_model_pricing", _fake_pricing)
    ident = client.post("/api/v1/agents", json={
        "name": "agent_identifier", "kind": "identifier"}).json()["id"]
    body = client.post("/api/v1/runs", json={
        "input_type": "transcription",
        "input_data": "Anita: salary talk\nBob: noted",
        "agent_ids": [ident], "model": "m"}).json()
    payload = seen["payload"]
    assert payload["messages"][1] == {
        "role": "user", "content": "[1] Anita: salary talk\n\n[2] Bob: noted"}
    assert "state" not in payload  # chat bodies carry no decisions state
    # {value, evidence} object schema per key.
    props = payload["response_format"]["json_schema"]["schema"]["properties"]
    assert props["has_assets"]["properties"]["value"] == {"type": "boolean"}
    assert props["has_assets"]["properties"]["evidence"] == {"type": ["integer", "null"]}
    # Stored outputs stay {key: bool}; sidecars land on the log.
    assert body["outputs"][ident]["has_assets"] is False
    log = client.get("/api/v1/logs").json()[0]
    assert log["evidence"] == {ident: {q["key"]: None for q in IDENTIFIER_QUESTIONS}}
    assert log["probabilities"] == {ident: {q["key"]: None for q in IDENTIFIER_QUESTIONS}}
    assert log["fireflies_url"] == ""


def test_decisions_identifier_request_chunks_state_and_sidecars(client, monkeypatch):
    import app.modules.runs.router as rr

    seen = {}

    async def _fake_decisions(payload):
        seen["payload"] = payload
        keys = [q["key"] for q in IDENTIFIER_QUESTIONS]
        answers = {k: {"type": "noul", "noul": 0.8} for k in keys}
        answers["tax"] = {"type": "noul", "noul": 0.2}
        answers["has_assets__evidence"] = {"type": "choice", "choice": "2"}
        return answers, {"prompt_tokens": 10, "completion_tokens": 5,
                         "total_tokens": 15, "reasoning_tokens": 0,
                         "provider": "pv", "cost": 0.001,
                         "cost_details": None,
                         "served_model": "typesafe/jev-1.13"}

    async def _no_chat(payload):
        raise AssertionError("chat must not be called for decision models")

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(rr, "complete_decisions_payload", _fake_decisions)
    monkeypatch.setattr(rr, "complete_json_payload", _no_chat)
    monkeypatch.setattr(rr, "get_model_pricing", _fake_pricing)
    ident = client.post("/api/v1/agents", json={
        "name": "agent_identifier", "kind": "identifier"}).json()["id"]
    body = client.post("/api/v1/runs", json={
        "input_type": "transcription",
        "input_data": "Anita: salary talk\nBob: noted",
        "agent_ids": [ident], "model": "typesafe/jev-1.13"}).json()
    # Same numbered chunks travel in decisions state (no raw transcript key).
    assert seen["payload"]["state"] == {
        "chunks": [{"n": 1, "text": "Anita: salary talk"},
                   {"n": 2, "text": "Bob: noted"}]}
    assert "has_assets__evidence" in seen["payload"]["questions"]
    out = body["outputs"][ident]
    assert out["has_assets"] is True and out["tax"] is False
    log = client.get("/api/v1/logs").json()[0]
    assert log["evidence"][ident]["has_assets"] == 2  # choice "2" -> chunk 2
    assert log["evidence"][ident]["tax"] is None  # false -> None
    assert log["probabilities"][ident]["has_assets"] == 0.8
    assert log["probabilities"][ident]["tax"] == 0.2


# --- chat max_tokens (identifier cap) ------------------------------------------


def test_build_chat_payload_max_tokens_default_omitted():
    p = build_chat_payload(model="m", system="s", user="u")
    assert "max_tokens" not in p
    assert set(p) == {"model", "messages", "response_format", "usage"}
    # Explicit None stays byte-identical to today.
    assert build_chat_payload(model="m", system="s", user="u", max_tokens=None) == p


def test_build_chat_payload_max_tokens_set():
    p = build_chat_payload(model="m", system="s", user="u", max_tokens=2048)
    assert p["max_tokens"] == 2048
    assert set(p) == {"model", "messages", "response_format", "usage", "max_tokens"}


def test_identifier_chat_request_carries_max_tokens(client, monkeypatch):
    import app.modules.runs.router as rr

    async def _fake(payload):
        return ({"ok": 1}, {"prompt_tokens": 1, "completion_tokens": 1,
                             "total_tokens": 2})

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(rr, "complete_json_payload", _fake)
    monkeypatch.setattr(rr, "get_model_pricing", _fake_pricing)
    ident = client.post("/api/v1/agents", json={
        "name": "agent_identifier", "kind": "identifier"}).json()["id"]
    ext = client.post("/api/v1/agents", json={"name": "R"}).json()["id"]
    client.post("/api/v1/attributes", json={"agent_ids": [ext], "name": "mood"})
    body = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [ident, ext], "model": "m"}).json()
    assert body["requests"][ident]["max_tokens"] == 2048
    assert "max_tokens" not in body["requests"][ext]


# --- 3-window fan-out for oversized decisions states ---------------------------


def _lines_input(n, pad=20):
    # N lines -> N chunks (turn-per-line), each line a fixed char count.
    return "\n".join(f"Speaker{i}: {'x' * pad}" for i in range(n))


def test_window_bounds_math():
    mod = runs_router
    assert mod._decisions_window_bounds(49) == (30, 19, 35, 26)
    assert mod._decisions_window_bounds(10) == (6, 4, 7, 5)
    for n in (3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 17, 20, 30, 49, 50, 100):
        b = mod._decisions_window_bounds(n)
        assert b is not None
        k1, k2, k3, k4 = b
        assert 1 <= k2 < k1 < k3 <= n
        assert k2 < k4 < k3
        # Each window non-empty and 1..N fully covered.
        assert k1 >= 1 and k3 >= k2 and n >= k4
        covered = (set(range(1, k1 + 1)) | set(range(k2, k3 + 1))
                   | set(range(k4, n + 1)))
        assert covered == set(range(1, n + 1))
        # P1/P3 overlap P2.
        assert set(range(1, k1 + 1)) & set(range(k2, k3 + 1))
        assert set(range(k4, n + 1)) & set(range(k2, k3 + 1))
    for bad in (0, 1, 2, -5, None, "x", True):
        assert mod._decisions_window_bounds(bad) is None
    assert mod._parse_split_formula("6,4,7,5", 10) == (6, 4, 7, 5)
    for bad in ("6,4,7", "6,4,7,5,1", "a,b,c,d", "6,6,7,5", "0,4,7,5",
                "6,4,7,11", None, ""):
        assert mod._parse_split_formula(bad, 10) is None


def _all_false_window(noul=0.1):
    keys = [q["key"] for q in IDENTIFIER_QUESTIONS]
    return {k: {"type": "noul", "noul": noul} for k in keys}


def _usage_window(prompt, completion, cost, provider, served):
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": prompt + completion, "reasoning_tokens": 0,
            "provider": provider, "cost": cost, "cost_details": None,
            "served_model": served}


def test_split_threshold_boundary_single_vs_three(client, monkeypatch):
    import app.modules.runs.router as rr

    monkeypatch.setattr(rr, "DECISIONS_SPLIT_STATE_CHARS", 60)
    calls = []

    async def _fake(payload):
        calls.append(dict(payload))
        return _all_false_window(), _usage_window(5, 1, 0.001, "pv", "m")

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(rr, "complete_decisions_payload", _fake)
    monkeypatch.setattr(rr, "get_model_pricing", _fake_pricing)
    ident = _make_identifier(client)

    def _run(text):
        calls.clear()
        body = client.post("/api/v1/runs", json={
            "input_type": "transcription", "input_data": text,
            "agent_ids": [ident], "model": "typesafe/jev-1.13"}).json()
        return body

    # Just under (29 chars): single call, transport marker only.
    body = _run("Anita: salary talk\nBob: noted")
    assert len(calls) == 1
    req = body["requests"][ident]
    assert req["transport"] == "decisions"
    assert "split_windows" not in req
    assert "split_formula" not in req

    # Exactly at threshold (60 chars): not exceeded -> single call.
    line = "Speaker0: " + "x" * 20
    assert len(line) == 30
    body = _run(line + "\n" + line.replace("Speaker0", "Speaker1"))
    assert len(calls) == 1
    req = body["requests"][ident]
    assert "split_windows" not in req
    assert "split_formula" not in req

    # Just over (90 chars, 3 chunks): 3 sequential calls + markers.
    body = _run("\n".join(line.replace("Speaker0", f"Speaker{i}") for i in range(3)))
    assert len(calls) == 3
    req = body["requests"][ident]
    assert req["transport"] == "decisions"
    assert req["split_windows"] == 3
    assert req["split_formula"] == "2,1,3,2"
    # Stored body is the FIRST window's body (chunks 1..2, global numbering).
    assert [c["n"] for c in req["state"]["chunks"]] == [1, 2]
    # Markers are storage-only: never POSTed.
    for posted in calls:
        assert "transport" not in posted
        assert "split_windows" not in posted
        assert "split_formula" not in posted
    # P1 POSTed byte-identical to the stored body minus markers.
    assert calls[0] == {k: v for k, v in req.items()
                        if k not in ("transport", "split_windows", "split_formula")}
    # Window first-chunk numbers: P1 starts 1, P2 starts 1, P3 starts 2.
    assert [p["state"]["chunks"][0]["n"] for p in calls] == [1, 1, 2]
    # All-false merge: clean output, no partial marker.
    out = body["outputs"][ident]
    assert all(v is False for k, v in out.items() if k != "partial")
    assert "partial" not in out


def _ten_chunk_window_answers(first_n):
    keys = [q["key"] for q in IDENTIFIER_QUESTIONS]
    answers = {k: {"type": "noul", "noul": 0.1} for k in keys}
    del answers["has_assets"]  # omitted by the model in every window
    if first_n == 1:  # P1 = chunks 1..6
        answers["income"] = {"type": "noul", "noul": 0.6}
        answers["income__evidence"] = {"type": "choice", "choice": "2"}
    elif first_n == 4:  # P2 = chunks 4..7
        answers["income"] = {"type": "noul", "noul": 0.9}
        answers["income__evidence"] = {"type": "choice", "choice": "7"}
        answers["insurance"] = {"type": "noul", "noul": 0.75}
        answers["insurance__evidence"] = {"type": "choice", "choice": "5"}
        answers["goals"] = {"type": "noul", "noul": 0.2}
    else:  # P3 = chunks 5..10
        assert first_n == 5
        answers["tax"] = {"type": "noul", "noul": 0.7}
        answers["tax__evidence"] = {"type": "choice", "choice": "10"}
        answers["goals"] = {"type": "noul", "noul": 0.15}
    return answers


def _ten_chunk_window_usage(first_n):
    table = {1: (100, 10, 0.001, "p1", "m1"),
             4: (200, 20, 0.002, "p2", "m2"),
             5: (300, 30, 0.003, "p3", "m3")}
    p, c, cost, prov, served = table[first_n]
    return _usage_window(p, c, cost, prov, served)


def test_split_merge_or_first_true_max_prob_and_usage(client, monkeypatch):
    import app.modules.runs.router as rr

    monkeypatch.setattr(rr, "DECISIONS_SPLIT_STATE_CHARS", 50)
    calls = []

    async def _fake(payload):
        calls.append(dict(payload))
        first_n = payload["state"]["chunks"][0]["n"]
        return (_ten_chunk_window_answers(first_n),
                _ten_chunk_window_usage(first_n))

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(rr, "complete_decisions_payload", _fake)
    monkeypatch.setattr(rr, "get_model_pricing", _fake_pricing)
    ident = _make_identifier(client)
    body = client.post("/api/v1/runs", json={
        "input_type": "transcription", "input_data": _lines_input(10),
        "agent_ids": [ident], "model": "typesafe/jev-1.13"}).json()

    assert len(calls) == 3
    # Windows cover 1..10 with global numbering.
    assert [[c["n"] for c in p["state"]["chunks"]] for p in calls] == [
        [1, 2, 3, 4, 5, 6], [4, 5, 6, 7], [5, 6, 7, 8, 9, 10]]
    # Per-window evidence criteria are limited to that window's chunks.
    p2_ev = calls[1]["questions"]["income__evidence"]
    assert set(p2_ev["criteria"]) == {"4", "5", "6", "7"}
    assert "(4..7)" in p2_ev["instructions"]
    req = body["requests"][ident]
    assert req["split_windows"] == 3
    assert req["split_formula"] == "6,4,7,5"
    assert [c["n"] for c in req["state"]["chunks"]] == [1, 2, 3, 4, 5, 6]
    assert set(req["questions"]["income__evidence"]["criteria"]) == {
        "1", "2", "3", "4", "5", "6"}

    out = body["outputs"][ident]
    # OR-merge: true in any window -> true.
    assert out["income"] is True
    assert out["insurance"] is True  # true only in P2
    assert out["tax"] is True  # true only in P3
    assert out["goals"] is False
    assert "partial" not in out  # all windows ok -> no marker

    log = client.get("/api/v1/logs").json()[0]
    ev = log["evidence"][ident]
    pr = log["probabilities"][ident]
    # First-true window (P1, P2, P3 order) decides the evidence chunk.
    assert ev["income"] == 2  # P1 and P2 true -> P1's chunk wins
    assert ev["insurance"] == 5
    assert ev["tax"] == 10
    assert ev["goals"] is None
    # Max noul float across windows; absent everywhere -> None.
    assert pr["income"] == 0.9
    assert pr["insurance"] == 0.75
    assert pr["tax"] == 0.7
    assert pr["goals"] == 0.2
    assert pr["has_assets"] is None

    # Usage sums; provider/served_model from the first successful window.
    per = body["usage"]["per_agent"][ident]
    assert per["prompt_tokens"] == 600
    assert per["completion_tokens"] == 60
    assert per["total_tokens"] == 660
    assert per["cost_usd"] == 0.006
    assert per["provider"] == "p1"
    assert per["served_model"] == "m1"
    assert isinstance(per["duration_ms"], float)

    # Reuse matching ignores the split markers (same first-window body).
    from app.modules.runs.router import _request_match_key
    clean_first = {k: v for k, v in req.items()
                   if k not in ("transport", "split_windows", "split_formula")}
    assert _request_match_key(req) == _request_match_key(clean_first)


def test_merge_helper_or_first_true_max_prob_round_trip():
    import app.modules.runs.router as rr

    w1 = _all_false_window()
    w1["income"] = {"type": "noul", "noul": 0.6}
    w1["income__evidence"] = {"type": "choice", "choice": "2"}
    w2 = _all_false_window()
    w2["income"] = {"type": "noul", "noul": 0.9}
    w2["income__evidence"] = {"type": "choice", "choice": "7"}
    w2["insurance"] = {"type": "noul", "noul": 0.75}
    w2["insurance__evidence"] = {"type": "choice", "choice": "5"}
    w3 = _all_false_window()
    w3["tax"] = {"type": "noul", "noul": 0.7}
    w3["tax__evidence"] = {"type": "choice", "choice": "10"}
    for w in (w1, w2, w3):
        del w["has_assets"]  # omitted by the model in every window
    merged = rr._merge_decisions_window_answers([w1, w2, w3], 10)
    bools, evidence, probs = split_identifier_result(merged, "decisions", 10)
    assert bools["income"] is True and evidence["income"] == 2
    assert probs["income"] == 0.9
    assert bools["insurance"] is True and evidence["insurance"] == 5
    assert probs["insurance"] == 0.75
    assert bools["tax"] is True and evidence["tax"] == 10
    assert probs["tax"] == 0.7
    assert bools["goals"] is False and evidence["goals"] is None
    assert probs["goals"] == 0.1
    # Absent everywhere -> False/None/None (key omitted from the raw merge).
    assert bools["has_assets"] is False and evidence["has_assets"] is None
    assert probs["has_assets"] is None
    assert "partial" not in merged


def test_split_all_fail_returns_last_error(client, monkeypatch):
    import app.modules.runs.router as rr

    monkeypatch.setattr(rr, "DECISIONS_SPLIT_STATE_CHARS", 50)
    calls = {"n": 0}

    async def _fake(payload):
        calls["n"] += 1
        raise RuntimeError(f"boom-{calls['n']}")

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(rr, "complete_decisions_payload", _fake)
    monkeypatch.setattr(rr, "get_model_pricing", _fake_pricing)
    ident = _make_identifier(client)
    body = client.post("/api/v1/runs", json={
        "input_type": "transcription", "input_data": _lines_input(10),
        "agent_ids": [ident], "model": "typesafe/jev-1.13"}).json()
    assert calls["n"] == 3
    assert body["outputs"][ident] == {"_error": "boom-3"}
    per = body["usage"]["per_agent"][ident]
    assert per["prompt_tokens"] == 0
    assert per["completion_tokens"] == 0
    assert per["cost_usd"] is None
    assert per["provider"] == ""
    # Markers are still stored (first-window body) for reuse matching.
    req = body["requests"][ident]
    assert req["split_windows"] == 3
    assert req["split_formula"] == "6,4,7,5"


def test_split_partial_merges_ok_windows_first_successful_provider(client, monkeypatch):
    import app.modules.runs.router as rr

    monkeypatch.setattr(rr, "DECISIONS_SPLIT_STATE_CHARS", 50)
    calls = []

    async def _fake(payload):
        first_n = payload["state"]["chunks"][0]["n"]
        calls.append(first_n)
        if first_n == 1:
            raise RuntimeError("p1-down")
        return (_ten_chunk_window_answers(first_n),
                _ten_chunk_window_usage(first_n))

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(rr, "complete_decisions_payload", _fake)
    monkeypatch.setattr(rr, "get_model_pricing", _fake_pricing)
    ident = _make_identifier(client)
    body = client.post("/api/v1/runs", json={
        "input_type": "transcription", "input_data": _lines_input(10),
        "agent_ids": [ident], "model": "typesafe/jev-1.13"}).json()
    assert calls == [1, 4, 5]  # all three attempted in order, sequentially
    out = body["outputs"][ident]
    # Merged from the ok windows (P2, P3); P1-only evidence is gone.
    assert out["partial"] is True
    assert out["income"] is True  # P2 true
    assert out["insurance"] is True
    assert out["tax"] is True
    assert out["goals"] is False
    log = client.get("/api/v1/logs").json()[0]
    ev = log["evidence"][ident]
    pr = log["probabilities"][ident]
    assert ev["income"] == 7  # first TRUE window now P2
    assert ev["tax"] == 10
    assert pr["income"] == 0.9
    per = body["usage"]["per_agent"][ident]
    # Usage sums the ok windows only; provider from first SUCCESSFUL window.
    assert per["prompt_tokens"] == 500
    assert per["completion_tokens"] == 50
    assert per["total_tokens"] == 550
    assert per["cost_usd"] == 0.005
    assert per["provider"] == "p2"
    assert per["served_model"] == "m2"


def test_questions_window_global_numbering_shape():
    import app.modules.runs.router as rr

    win = [{"n": 4, "text": "d"}, {"n": 5, "text": "e"},
           {"n": 6, "text": "f"}, {"n": 7, "text": "g"}]
    questions = rr.build_identifier_decisions_questions(win)
    assert len(questions) == 2 * len(IDENTIFIER_QUESTIONS) == 54
    ev = questions["income__evidence"]
    assert ev["criteria"] == {"4": "chunk 4", "5": "chunk 5",
                              "6": "chunk 6", "7": "chunk 7"}
    assert "(4..7)" in ev["instructions"]
    # Full contiguous chunks stay byte-identical to the old positional shape.
    full = [{"n": i + 1, "text": f"turn {i + 1}"} for i in range(3)]
    q2 = rr.build_identifier_decisions_questions(full)
    assert q2["has_assets__evidence"]["criteria"] == {
        "1": "chunk 1", "2": "chunk 2", "3": "chunk 3"}
