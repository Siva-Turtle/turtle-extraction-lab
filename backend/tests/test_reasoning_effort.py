"""Reasoning effort + thought tokens (synthetic only, no live keys/PII)."""

import asyncio
import json

import app.core.openrouter as openrouter
import app.modules.runs.router as runs_router
from app.core.config import settings
from app.core.openrouter import REASONING_EFFORTS, _extract_usage, build_chat_payload


def test_reasoning_efforts_constant():
    assert REASONING_EFFORTS == ("max", "xhigh", "high", "medium", "low", "minimal", "none")


def test_build_chat_payload_reasoning_effort():
    p = build_chat_payload(model="m", system="s", user="u", reasoning_effort="high")
    assert p["reasoning"] == {"effort": "high"}
    # Other keys unchanged.
    assert p["model"] == "m"
    assert p["messages"] == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]


def test_build_chat_payload_omits_reasoning_when_blank():
    for blank in (None, "", "   "):
        p = build_chat_payload(model="m", system="s", user="u", reasoning_effort=blank)
        assert "reasoning" not in p
        # Exact-shape contract for existing callers.
        assert set(p) == {"model", "messages", "response_format"}


def test_build_chat_payload_reasoning_strips():
    p = build_chat_payload(model="m", system="s", user="u", reasoning_effort="  low  ")
    assert p["reasoning"] == {"effort": "low"}


def test_extract_usage_reasoning_tokens():
    u = _extract_usage({"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15,
                        "completion_tokens_details": {"reasoning_tokens": 17}})
    assert u == {"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15,
                 "reasoning_tokens": 17}
    # Absent block means 0.
    assert _extract_usage({"prompt_tokens": 1})["reasoning_tokens"] == 0
    assert _extract_usage({})["reasoning_tokens"] == 0
    assert _extract_usage(None)["reasoning_tokens"] == 0
    assert _extract_usage("garbage")["reasoning_tokens"] == 0
    # Non-dict / negative / garbage details mean 0.
    assert _extract_usage({"completion_tokens_details": "x"})["reasoning_tokens"] == 0
    assert _extract_usage(
        {"completion_tokens_details": {"reasoning_tokens": -4}})["reasoning_tokens"] == 0
    assert _extract_usage(
        {"completion_tokens_details": {"reasoning_tokens": "oops"}})["reasoning_tokens"] == 0


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _fake_client(payload):
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            return _FakeResp(payload)

    return _FakeClient


def test_complete_json_picks_up_reasoning_tokens(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    body = {"choices": [{"message": {"content": json.dumps({"a": 1})}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 9, "total_tokens": 12,
                      "completion_tokens_details": {"reasoning_tokens": 17}}}
    monkeypatch.setattr(openrouter.httpx, "AsyncClient", _fake_client(body))
    _, usage = asyncio.run(openrouter.complete_json(model="m", system="s", user="u"))
    assert usage["reasoning_tokens"] == 17
    assert usage == {"prompt_tokens": 3, "completion_tokens": 9, "total_tokens": 12,
                     "reasoning_tokens": 17}


def _make_agent(client):
    aid = client.post("/api/v1/agents", json={"name": "R"}).json()["id"]
    client.post("/api/v1/attributes", json={"agent_ids": [aid], "name": "mood"})
    return aid


def test_run_with_reasoning_effort_stores_and_sums(client, monkeypatch):
    aid = _make_agent(client)
    seen = {}

    async def _fake(payload):
        seen["payload"] = payload
        return ({"ok": 1}, {"prompt_tokens": 10, "completion_tokens": 20,
                            "total_tokens": 30, "reasoning_tokens": 17})

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(runs_router, "complete_json_payload", _fake)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)
    body = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [aid], "model": "m", "reasoning_effort": "low"}).json()
    assert body["usage"]["reasoning_tokens"] == 17
    assert body["usage"]["per_agent"][aid]["reasoning_tokens"] == 17
    # Reasoning tokens are inside completion tokens — total unchanged.
    assert body["usage"]["total_tokens"] == body["usage"]["prompt_tokens"] + body["usage"]["completion_tokens"]
    assert seen["payload"]["reasoning"] == {"effort": "low"}
    logs = client.get("/api/v1/logs").json()
    assert logs[0]["reasoning_effort"] == "low"


def test_run_with_blank_effort_sends_nothing(client, monkeypatch):
    aid = _make_agent(client)
    seen = {}

    async def _fake(payload):
        seen["payload"] = payload
        return ({"ok": 1}, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(runs_router, "complete_json_payload", _fake)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)
    body = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [aid], "model": "m"}).json()
    assert "reasoning" not in seen["payload"]
    assert body["usage"]["reasoning_tokens"] == 0
    assert body["usage"]["per_agent"][aid]["reasoning_tokens"] == 0
    assert client.get("/api/v1/logs").json()[0]["reasoning_effort"] == ""


def test_run_invalid_reasoning_effort_422(client):
    resp = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [], "model": "m", "reasoning_effort": "bogus"})
    assert resp.status_code == 422


def test_run_error_path_reasoning_zero(client, monkeypatch):
    aid = _make_agent(client)

    async def _boom(payload):
        raise RuntimeError("provider down")

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)
    body = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [aid], "model": "m", "reasoning_effort": "high"}).json()
    assert body["usage"]["per_agent"][aid]["reasoning_tokens"] == 0
    assert body["usage"]["reasoning_tokens"] == 0


def test_logs_filter_by_reasoning_efforts(client, monkeypatch):
    async def _fake(payload):
        return ({"ok": 1}, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(runs_router, "complete_json_payload", _fake)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)
    for effort in ("low", "high"):
        client.post("/api/v1/runs", json={
            "input_type": "mail", "input_data": "hello",
            "agent_ids": [], "model": "m", "reasoning_effort": effort})
    assert len(client.get("/api/v1/logs").json()) == 2
    only = client.get("/api/v1/logs", params={"reasoning_efforts": "low"}).json()
    assert len(only) == 1 and only[0]["reasoning_effort"] == "low"
    both = client.get(
        "/api/v1/logs",
        params=[("reasoning_efforts", "low"), ("reasoning_efforts", "high")]).json()
    assert len(both) == 2
    assert client.get("/api/v1/logs", params={"reasoning_efforts": "nope"}).json() == []
    bracket = client.get("/api/v1/logs?reasoning_efforts[]=low").json()
    assert len(bracket) == 1 and bracket[0]["reasoning_effort"] == "low"
