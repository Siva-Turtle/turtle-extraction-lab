"""OpenRouter provider pinning (synthetic only, no live keys/PII)."""

import asyncio
import json

import app.core.openrouter as openrouter
import app.modules.runs.router as runs_router
from app.core.config import settings
from app.core.openrouter import build_chat_payload


def test_build_chat_payload_without_provider_byte_identical():
    for blank in (None, "", "   "):
        p = build_chat_payload(model="m", system="s", user="u", provider=blank)
        assert "provider" not in p
        assert set(p) == {"model", "messages", "response_format"}
    # Default (no provider kwarg) is also byte-identical.
    p0 = build_chat_payload(model="m", system="s", user="u")
    p1 = build_chat_payload(model="m", system="s", user="u", provider="")
    assert p0 == p1


def test_build_chat_payload_with_provider():
    p = build_chat_payload(model="m", system="s", user="u", provider="deepinfra")
    assert p["provider"] == {"order": ["deepinfra"], "allow_fallbacks": False}
    # Strips whitespace.
    p2 = build_chat_payload(model="m", system="s", user="u", provider="  deepinfra  ")
    assert p2["provider"] == {"order": ["deepinfra"], "allow_fallbacks": False}
    # Combines with reasoning effort.
    p3 = build_chat_payload(
        model="m", system="s", user="u",
        reasoning_effort="high", provider="deepinfra")
    assert p3["reasoning"] == {"effort": "high"}
    assert p3["provider"] == {"order": ["deepinfra"], "allow_fallbacks": False}


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _fake_get_client(payload=None, exc=None, seen=None):
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            if seen is not None:
                seen["url"] = url
                seen["headers"] = headers
            if exc is not None:
                raise exc
            return _FakeResp(payload)

    return _FakeClient


def test_fetch_model_endpoints_normalization(monkeypatch):
    openrouter.clear_endpoints_cache()
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_base_url", "https://openrouter.ai/api/v1")
    seen: dict = {}
    payload = {"data": {"endpoints": [
        {"provider_name": "DeepInfra", "tag": "deepinfra",
         "quantization": "fp8", "context_length": 128000,
         "pricing": {"prompt": "0.0000001", "completion": "0.0000002"},
         "uptime_last_30m": 99.5, "status": 1},
        {"provider_name": "Together",
         "pricing": {"prompt": "oops", "completion": None}},
        "not-a-dict",
    ]}}
    monkeypatch.setattr(openrouter.httpx, "AsyncClient", _fake_get_client(payload, seen=seen))
    out = asyncio.run(openrouter.fetch_model_endpoints("author/slug"))
    assert seen["url"] == "https://openrouter.ai/api/v1/models/author/slug/endpoints"
    assert seen["headers"]["Authorization"] == "Bearer test-key"
    assert len(out) == 2
    assert out[0] == {
        "slug": "deepinfra", "name": "DeepInfra", "tag": "deepinfra",
        "quantization": "fp8", "context_length": 128000,
        "pricing": {"prompt": 0.0000001, "completion": 0.0000002},
        "uptime_last_30m": 99.5, "status": 1,
    }
    # No tag -> slug is lowercased provider name; bad pricing -> None.
    assert out[1]["slug"] == "together"
    assert out[1]["name"] == "Together"
    assert out[1]["tag"] == ""
    assert out[1]["quantization"] is None
    assert out[1]["context_length"] is None
    assert out[1]["pricing"] == {"prompt": None, "completion": None}
    assert out[1]["uptime_last_30m"] is None
    assert out[1]["status"] is None
    openrouter.clear_endpoints_cache()


def test_fetch_model_endpoints_failure_returns_empty(monkeypatch):
    openrouter.clear_endpoints_cache()
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(
        openrouter.httpx, "AsyncClient",
        _fake_get_client(exc=RuntimeError("boom")))
    assert asyncio.run(openrouter.fetch_model_endpoints("a/b")) == []
    openrouter.clear_endpoints_cache()


def test_fetch_model_endpoints_not_configured(monkeypatch):
    openrouter.clear_endpoints_cache()
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    # Must not touch the network.
    def _boom(*a, **k):
        raise AssertionError("must not call network")

    monkeypatch.setattr(openrouter.httpx, "AsyncClient", _boom)
    assert asyncio.run(openrouter.fetch_model_endpoints("a/b")) == []
    openrouter.clear_endpoints_cache()


def _fake_post_client(payload):
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


def test_complete_json_payload_provider_served(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    body = {"choices": [{"message": {"content": json.dumps({"a": 1})}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            "provider": "DeepInfra"}
    monkeypatch.setattr(openrouter.httpx, "AsyncClient", _fake_post_client(body))
    _, usage = asyncio.run(openrouter.complete_json(model="m", system="s", user="u"))
    assert usage["provider"] == "DeepInfra"
    # Absent provider -> "".
    body2 = {"choices": [{"message": {"content": json.dumps({"a": 1})}}]}
    monkeypatch.setattr(openrouter.httpx, "AsyncClient", _fake_post_client(body2))
    _, usage2 = asyncio.run(openrouter.complete_json(model="m", system="s", user="u"))
    assert usage2["provider"] == ""


def _make_agent(client):
    aid = client.post("/api/v1/agents", json={"name": "P"}).json()["id"]
    client.post("/api/v1/attributes", json={"agent_ids": [aid], "name": "mood"})
    return aid


def test_run_with_provider_stored_and_served(client, monkeypatch):
    aid = _make_agent(client)
    seen: dict = {}

    async def _fake(payload):
        seen["payload"] = payload
        return ({"ok": 1}, {"prompt_tokens": 1, "completion_tokens": 1,
                            "total_tokens": 2, "provider": "DeepInfra"})

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(runs_router, "complete_json_payload", _fake)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)
    body = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [aid], "model": "m", "provider": "deepinfra"}).json()
    assert seen["payload"]["provider"] == {"order": ["deepinfra"], "allow_fallbacks": False}
    assert body["usage"]["provider_requested"] == "deepinfra"
    assert body["usage"]["per_agent"][aid]["provider"] == "DeepInfra"
    assert body["requests"][aid]["provider"] == {"order": ["deepinfra"], "allow_fallbacks": False}
    logs = client.get("/api/v1/logs").json()
    assert logs[0]["provider"] == "deepinfra"
    assert logs[0]["usage"]["provider_requested"] == "deepinfra"
    assert logs[0]["usage"]["per_agent"][aid]["provider"] == "DeepInfra"


def test_run_auto_provider_defaults_empty(client, monkeypatch):
    aid = _make_agent(client)

    async def _fake(payload):
        assert "provider" not in payload
        return ({"ok": 1}, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(runs_router, "complete_json_payload", _fake)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)
    body = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [aid], "model": "m"}).json()
    assert body["usage"]["provider_requested"] == ""
    assert body["usage"]["per_agent"][aid]["provider"] == ""
    assert client.get("/api/v1/logs").json()[0]["provider"] == ""


def _check(client, aid, model, effort="", provider="", input_data="hello"):
    body = {"input_type": "mail", "input_data": input_data,
            "agent_ids": [aid],
            "models": [{"model": model, "reasoning_effort": effort, "provider": provider}]}
    resp = client.post("/api/v1/runs/check-existing", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_check_existing_respects_provider(client, monkeypatch):
    aid = _make_agent(client)

    async def _fake(payload):
        return ({"ok": 1}, {"prompt_tokens": 1, "completion_tokens": 1,
                            "total_tokens": 2, "provider": "DeepInfra"})

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(runs_router, "complete_json_payload", _fake)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)
    auto_log = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [aid], "model": "m"}).json()["log_id"]

    async def _boom(payload):
        raise AssertionError("check-existing must not call OpenRouter")

    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)
    # Auto run matches Auto check; pinned check finds nothing yet.
    auto_hit = _check(client, aid, "m")["slots"][0]["agents"]
    assert len(auto_hit) == 1 and auto_hit[0]["log_id"] == auto_log
    assert _check(client, aid, "m", provider="deepinfra")["slots"][0]["agents"] == []

    # Pinned run matches pinned check only (Auto check still hits the Auto log).
    async def _fake2(payload):
        return ({"ok": 1}, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    monkeypatch.setattr(runs_router, "complete_json_payload", _fake2)
    pinned_log = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [aid], "model": "m", "provider": "deepinfra"}).json()["log_id"]
    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)
    pinned_hit = _check(client, aid, "m", provider="deepinfra")["slots"][0]["agents"]
    assert len(pinned_hit) == 1 and pinned_hit[0]["log_id"] == pinned_log
    auto_hit2 = _check(client, aid, "m")["slots"][0]["agents"]
    assert len(auto_hit2) == 1 and auto_hit2[0]["log_id"] == auto_log


def test_models_endpoints_route(client, monkeypatch):
    async def _fake_endpoints(model_id: str):
        assert model_id == "a/b"
        return [{"slug": "deepinfra", "name": "DeepInfra"}]

    monkeypatch.setattr(openrouter, "fetch_model_endpoints", _fake_endpoints)
    body = client.get("/api/v1/models/endpoints", params={"model": "a/b"}).json()
    assert body == {"model": "a/b", "endpoints": [{"slug": "deepinfra", "name": "DeepInfra"}]}
