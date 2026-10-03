"""Usage/cost/timing + filters (synthetic only, no live keys/PII)."""

import asyncio
import json

import pytest

import app.core.openrouter as openrouter
import app.modules.runs.router as runs_router
from app.core.config import settings
from app.modules.runs.schemas import RunCreate


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _fake_client_factory(payload=None, exc=None, seen=None):
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            if seen is not None:
                seen["post_url"] = url
                seen["post_json"] = json
            if exc is not None:
                raise exc
            return _FakeResp(payload)

        async def get(self, url, headers=None):
            if seen is not None:
                seen["get_url"] = url
                seen.setdefault("get_count", 0)
                seen["get_count"] += 1
            if exc is not None:
                raise exc
            return _FakeResp(payload)

    return _FakeClient


def _chat_payload(content, usage=None):
    body = {"choices": [{"message": {"content": content}}]}
    if usage is not None:
        body["usage"] = usage
    return body


def test_complete_json_usage_full(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    payload = _chat_payload(json.dumps({"a": 1}),
                            {"prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46})
    monkeypatch.setattr(openrouter.httpx, "AsyncClient", _fake_client_factory(payload))
    parsed, usage = asyncio.run(openrouter.complete_json(model="m", system="s", user="u"))
    assert parsed == {"a": 1}
    assert usage == {"prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46,
                     "reasoning_tokens": 0, "provider": "",
                     "cost": None, "cost_details": None, "served_model": ""}


def test_complete_json_usage_missing_is_zeros(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(openrouter.httpx, "AsyncClient",
                        _fake_client_factory(_chat_payload(json.dumps({"a": 1}))))
    _, usage = asyncio.run(openrouter.complete_json(model="m", system="s", user="u"))
    assert usage == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                     "reasoning_tokens": 0, "provider": "",
                     "cost": None, "cost_details": None, "served_model": ""}


def test_complete_json_usage_partial_is_zeros(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    payload = _chat_payload(json.dumps({"a": 1}), {"prompt_tokens": 5})
    monkeypatch.setattr(openrouter.httpx, "AsyncClient", _fake_client_factory(payload))
    _, usage = asyncio.run(openrouter.complete_json(model="m", system="s", user="u"))
    assert usage == {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 0,
                     "reasoning_tokens": 0, "provider": "",
                     "cost": None, "cost_details": None, "served_model": ""}


def test_complete_json_usage_reasoning_tokens_from_details(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    payload = _chat_payload(
        json.dumps({"a": 1}),
        {"prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46,
         "completion_tokens_details": {"reasoning_tokens": 7}})
    monkeypatch.setattr(openrouter.httpx, "AsyncClient", _fake_client_factory(payload))
    _, usage = asyncio.run(openrouter.complete_json(model="m", system="s", user="u"))
    assert usage == {"prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46,
                     "reasoning_tokens": 7, "provider": "",
                     "cost": None, "cost_details": None, "served_model": ""}


def test_complete_json_usage_reasoning_tokens_fallback_top_level(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    payload = _chat_payload(
        json.dumps({"a": 1}),
        {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3,
         "reasoning_tokens": 9})
    monkeypatch.setattr(openrouter.httpx, "AsyncClient", _fake_client_factory(payload))
    _, usage = asyncio.run(openrouter.complete_json(model="m", system="s", user="u"))
    assert usage["reasoning_tokens"] == 9


def test_complete_json_response_format_modes(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    seen: dict = {}
    payload = _chat_payload(json.dumps({"a": 1}))
    monkeypatch.setattr(openrouter.httpx, "AsyncClient", _fake_client_factory(payload, seen=seen))
    asyncio.run(openrouter.complete_json(model="m", system="s", user="u"))
    assert seen["post_json"]["response_format"] == {"type": "json_object"}
    schema = {"type": "object", "properties": {"mood": {
        "type": "string", "description": "Mood", "enum": ["good", "bad"]}},
        "required": [], "additionalProperties": False}
    asyncio.run(openrouter.complete_json(model="m", system="s", user="u", json_schema=schema))
    assert seen["post_json"]["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "meeting_extraction", "strict": True, "schema": schema}}


def test_pricing_unknown_without_key(monkeypatch):
    openrouter.clear_pricing_cache()
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    assert asyncio.run(openrouter.get_model_pricing("any")) == (None, None)


def test_pricing_known_via_stubbed_models(monkeypatch):
    openrouter.clear_pricing_cache()
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    seen: dict = {}
    payload = {"data": [
        {"id": "test-model", "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
        {"id": "bad-model", "pricing": {"prompt": "oops", "completion": None}},
    ]}
    monkeypatch.setattr(openrouter.httpx, "AsyncClient", _fake_client_factory(payload, seen=seen))
    assert asyncio.run(openrouter.get_model_pricing("test-model")) == (0.000001, 0.000002)
    # Unknown model resolves from the same valid cache without a refetch.
    assert asyncio.run(openrouter.get_model_pricing("nope")) == (None, None)
    assert seen["get_count"] == 1
    # Malformed pricing entry -> (None, None), never raises.
    assert asyncio.run(openrouter.get_model_pricing("bad-model")) == (None, None)
    openrouter.clear_pricing_cache()


def test_pricing_failure_returns_none(monkeypatch):
    openrouter.clear_pricing_cache()
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(openrouter.httpx, "AsyncClient",
                        _fake_client_factory(exc=RuntimeError("boom")))
    assert asyncio.run(openrouter.get_model_pricing("test-model")) == (None, None)
    openrouter.clear_pricing_cache()


def _make_agent(client):
    aid = client.post("/api/v1/agents", json={"name": "U"}).json()["id"]
    client.post("/api/v1/attributes", json={"agent_ids": [aid], "name": "email"})
    return aid


def test_run_usage_pricing_unknown_cost_none(client, monkeypatch):
    aid = _make_agent(client, )

    async def _fake_complete(payload):
        return ({"email": {"value": "x", "confidence": 1.0,
                           "confidence_type": "quoted", "evidence": "e"}},
                {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(runs_router, "complete_json_payload", _fake_complete)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)
    body = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [aid], "model": "test-model",
        "filters": {"client_id": "SYNTH001", "meeting_title": "Synthetic Review"}}).json()
    usage = body["usage"]
    assert usage["prompt_tokens"] == 10
    assert usage["completion_tokens"] == 5
    assert usage["total_tokens"] == 15
    assert usage["cost_usd"] is None
    assert usage["input_cost_usd"] is None
    assert usage["output_cost_usd"] is None
    assert usage["model"] == "test-model"
    assert usage["duration_ms"] >= 0
    per = usage["per_agent"][aid]
    assert per["prompt_tokens"] == 10 and per["completion_tokens"] == 5
    assert per["cost_usd"] is None and per["duration_ms"] >= 0 and per["model"] == "test-model"
    assert per["input_cost_usd"] is None
    assert per["output_cost_usd"] is None

    logs = client.get("/api/v1/logs").json()
    assert logs[0]["usage"] == usage
    assert logs[0]["filters"] == {"client_id": "SYNTH001", "meeting_title": "Synthetic Review"}


def test_run_usage_pricing_known_exact_math(client, monkeypatch):
    aid = _make_agent(client)

    async def _fake_complete(payload):
        return ({"ok": 1}, {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30})

    async def _fake_pricing(model):
        assert model == "test-model"
        return (0.000001, 0.000002)

    monkeypatch.setattr(runs_router, "complete_json_payload", _fake_complete)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)
    usage = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [aid], "model": "test-model"}).json()["usage"]
    # 10 * 1e-6 + 20 * 2e-6 = 0.00005 exactly.
    assert usage["per_agent"][aid]["cost_usd"] == pytest.approx(0.00005)
    assert usage["cost_usd"] == pytest.approx(0.00005)
    # Split: 10 * 1e-6 = 0.00001 in, 20 * 2e-6 = 0.00004 out.
    assert usage["per_agent"][aid]["input_cost_usd"] == pytest.approx(0.00001)
    assert usage["per_agent"][aid]["output_cost_usd"] == pytest.approx(0.00004)
    assert usage["input_cost_usd"] == pytest.approx(0.00001)
    assert usage["output_cost_usd"] == pytest.approx(0.00004)


def test_run_error_agent_zeros(client, monkeypatch):
    aid_ok = _make_agent(client)
    aid_bad = _make_agent(client)

    # Deterministic: first agent succeeds, second fails.
    calls = {"n": 0}

    async def _ordered(payload):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("provider down")
        return ({"ok": 1}, {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10})

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(runs_router, "complete_json_payload", _ordered)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)
    body = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [aid_ok, aid_bad], "model": "m"}).json()
    # Exactly one agent failed regardless of DB return order.
    failed = [aid for aid in (aid_ok, aid_bad) if "_error" in body["outputs"][aid]]
    succeeded = [aid for aid in (aid_ok, aid_bad) if "_error" not in body["outputs"][aid]]
    assert len(failed) == 1 and len(succeeded) == 1
    bad = body["usage"]["per_agent"][failed[0]]
    assert bad["prompt_tokens"] == 0 and bad["completion_tokens"] == 0 and bad["total_tokens"] == 0
    assert bad["cost_usd"] is None and bad["duration_ms"] >= 0
    assert bad["input_cost_usd"] is None and bad["output_cost_usd"] is None
    # Totals only count the successful agent.
    assert body["usage"]["prompt_tokens"] == 4
    assert body["usage"]["completion_tokens"] == 6
    assert body["usage"]["cost_usd"] is None
    assert body["usage"]["input_cost_usd"] is None
    assert body["usage"]["output_cost_usd"] is None


def test_run_legacy_payload_without_filters_defaults(client, monkeypatch):
    aid = _make_agent(client)

    async def _fake_complete(payload):
        return ({"ok": 1}, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(runs_router, "complete_json_payload", _fake_complete)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)
    body = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [aid], "model": "m"}).json()
    assert "usage" in body
    logs = client.get("/api/v1/logs").json()
    assert logs[0]["filters"] == {}
    assert logs[0]["usage"]["total_tokens"] == 2


def test_runcreate_non_dict_filters_coerced():
    assert RunCreate(model="m", filters="oops").filters == {}  # type: ignore[arg-type]
    assert RunCreate(model="m").filters == {}
    assert RunCreate(model="m", filters={"a": 1}).filters == {"a": 1}


def test_run_usage_split_totals_multi_agent(client, monkeypatch):
    aid1 = _make_agent(client)
    aid2 = _make_agent(client)
    calls = {"n": 0}

    async def _fake_complete(payload):
        # DB return order is not guaranteed — vary tokens by call order and
        # map assertions via the recorded prompt_tokens below.
        calls["n"] += 1
        if calls["n"] == 1:
            return ({"ok": 1}, {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30})
        return ({"ok": 1}, {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105})

    async def _fake_pricing(model):
        return (0.000001, 0.000002)

    monkeypatch.setattr(runs_router, "complete_json_payload", _fake_complete)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)
    body = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [aid1, aid2], "model": "test-model"}).json()
    usage = body["usage"]
    assert {usage["per_agent"][aid1]["prompt_tokens"],
            usage["per_agent"][aid2]["prompt_tokens"]} == {10, 100}
    # Each entry's split follows its own token counts mechanically.
    for aid in (aid1, aid2):
        per = usage["per_agent"][aid]
        assert per["input_cost_usd"] == pytest.approx(round(per["prompt_tokens"] * 0.000001, 6))
        assert per["output_cost_usd"] == pytest.approx(round(per["completion_tokens"] * 0.000002, 6))
    # Totals are the rounded sums: in 10*1e-6 + 100*1e-6 = 0.00011,
    # out 20*2e-6 + 5*2e-6 = 0.00005.
    assert usage["input_cost_usd"] == pytest.approx(0.00011)
    assert usage["output_cost_usd"] == pytest.approx(0.00005)
    # Existing combined total unchanged: 0.00005 + 0.00011 = 0.00016.
    assert usage["cost_usd"] == pytest.approx(0.00016)


def test_run_usage_partial_pricing_input_known_output_unknown(client, monkeypatch):
    aid = _make_agent(client)

    async def _fake_complete(payload):
        return ({"ok": 1}, {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30})

    async def _fake_pricing(model):
        return (0.000001, None)

    monkeypatch.setattr(runs_router, "complete_json_payload", _fake_complete)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)
    usage = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [aid], "model": "test-model"}).json()["usage"]
    assert usage["per_agent"][aid]["input_cost_usd"] == pytest.approx(0.00001)
    assert usage["per_agent"][aid]["output_cost_usd"] is None
    assert usage["input_cost_usd"] == pytest.approx(0.00001)
    assert usage["output_cost_usd"] is None
    assert usage["cost_usd"] is None


def test_run_usage_partial_pricing_output_known_input_unknown(client, monkeypatch):
    aid = _make_agent(client)

    async def _fake_complete(payload):
        return ({"ok": 1}, {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30})

    async def _fake_pricing(model):
        return (None, 0.000002)

    monkeypatch.setattr(runs_router, "complete_json_payload", _fake_complete)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)
    usage = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [aid], "model": "test-model"}).json()["usage"]
    assert usage["per_agent"][aid]["input_cost_usd"] is None
    assert usage["per_agent"][aid]["output_cost_usd"] == pytest.approx(0.00004)
    assert usage["input_cost_usd"] is None
    assert usage["output_cost_usd"] == pytest.approx(0.00004)
    assert usage["cost_usd"] is None


def test_run_error_agent_known_pricing_zero_costs(client, monkeypatch):
    aid_ok = _make_agent(client)
    aid_bad = _make_agent(client)
    calls = {"n": 0}

    async def _ordered(payload):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("provider down")
        return ({"ok": 1}, {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10})

    async def _fake_pricing(model):
        return (0.000001, 0.000002)

    monkeypatch.setattr(runs_router, "complete_json_payload", _ordered)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)
    body = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [aid_ok, aid_bad], "model": "m"}).json()
    failed = [aid for aid in (aid_ok, aid_bad) if "_error" in body["outputs"][aid]]
    assert len(failed) == 1
    bad = body["usage"]["per_agent"][failed[0]]
    # Zero tokens with known pricing stay 0.0, never None.
    assert bad["input_cost_usd"] == 0.0
    assert bad["output_cost_usd"] == 0.0
    assert bad["cost_usd"] == pytest.approx(0.0)
    # Totals still aggregate (failed agent contributes 0.0).
    assert body["usage"]["input_cost_usd"] == pytest.approx(0.000004)
    assert body["usage"]["output_cost_usd"] == pytest.approx(0.000012)


def test_run_reasoning_effort_high_flows_to_requests_usage_logs(client, monkeypatch):
    aid = _make_agent(client)
    seen: dict = {}

    async def _fake_complete(payload):
        seen["payload"] = payload
        return ({"ok": 1}, {"prompt_tokens": 10, "completion_tokens": 20,
                            "total_tokens": 30, "reasoning_tokens": 7})

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(runs_router, "complete_json_payload", _fake_complete)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)
    body = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [aid], "model": "m", "reasoning_effort": "high"}).json()
    assert body["requests"][aid]["reasoning"] == {"effort": "high"}
    assert seen["payload"]["reasoning"] == {"effort": "high"}
    assert body["usage"]["reasoning_tokens"] == 7
    assert body["usage"]["per_agent"][aid]["reasoning_tokens"] == 7
    logs = client.get("/api/v1/logs").json()
    assert logs[0]["reasoning_effort"] == "high"
    assert len(client.get("/api/v1/logs", params={"reasoning_efforts": "high"}).json()) == 1
    assert client.get("/api/v1/logs", params={"reasoning_efforts": "low"}).json() == []

    # Blank effort sends NO reasoning key and logs "".
    seen.clear()

    async def _fake_blank(payload):
        seen["payload"] = payload
        return ({"ok": 1}, {"prompt_tokens": 1, "completion_tokens": 1,
                            "total_tokens": 2, "reasoning_tokens": 0})

    monkeypatch.setattr(runs_router, "complete_json_payload", _fake_blank)
    body2 = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [aid], "model": "m", "reasoning_effort": ""}).json()
    assert "reasoning" not in body2["requests"][aid]
    assert "reasoning" not in seen["payload"]
    logs2 = client.get("/api/v1/logs").json()
    blank = [l for l in logs2 if l["run_id"] == body2["id"]] if "id" in body2 else logs2
    # Newest-first ordering puts the blank run at index 0.
    assert logs2[0]["reasoning_effort"] == ""


def test_usage_cost_preferred_over_catalog(client, monkeypatch):
    aid = _make_agent(client)

    async def _fake_complete(payload):
        return ({"ok": 1}, {"prompt_tokens": 10, "completion_tokens": 20,
                             "total_tokens": 30, "cost": 0.123456789,
                             "cost_details": {"upstream_inference_cost": 0.1}})

    async def _fake_pricing(model):
        return (0.000001, 0.000002)

    monkeypatch.setattr(runs_router, "complete_json_payload", _fake_complete)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)
    usage = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [aid], "model": "test-model"}).json()["usage"]
    per = usage["per_agent"][aid]
    # Actual usage.cost wins (rounded to 6dp), not 10*1e-6+20*2e-6=0.00005.
    assert per["cost_usd"] == pytest.approx(0.123457)
    assert usage["cost_usd"] == pytest.approx(0.123457)
    # Input/output splits still come from the catalog price.
    assert per["input_cost_usd"] == pytest.approx(0.00001)
    assert per["output_cost_usd"] == pytest.approx(0.00004)


def test_negative_catalog_price_is_unknown():
    # "-1" (dynamic router) is never a price.
    assert runs_router._valid_price("-1") is None
    assert runs_router._valid_price(-1.0) is None
    assert runs_router._valid_price(None) is None
    assert runs_router._valid_price(0.000001) == pytest.approx(0.000001)
    per = {}
    aid = "a1"
    per[aid] = runs_router._per_agent_entry(10, 20, 30, 0, 1.0, "m", "")
    runs_router._fill_pricing(per, -1.0, -1.0)
    assert per[aid]["cost_usd"] is None
    assert per[aid]["input_cost_usd"] is None
    assert per[aid]["output_cost_usd"] is None


def test_negative_pricing_via_models_returns_none(monkeypatch):
    openrouter.clear_pricing_cache()
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    payload = {"data": [
        {"id": "typesafe/jev-router",
         "pricing": {"prompt": "-1", "completion": "-1"}},
    ]}
    monkeypatch.setattr(openrouter.httpx, "AsyncClient", _fake_client_factory(payload))
    assert asyncio.run(openrouter.get_model_pricing("typesafe/jev-router")) == (None, None)
    openrouter.clear_pricing_cache()


def test_served_model_recorded_when_differs(client, monkeypatch):
    aid = _make_agent(client)

    async def _fake_complete(payload):
        return ({"ok": 1}, {"prompt_tokens": 5, "completion_tokens": 5,
                             "total_tokens": 10, "cost": 0.01,
                             "served_model": "openai/gpt-4o"})

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(runs_router, "complete_json_payload", _fake_complete)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)
    body = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [aid], "model": "typesafe/jev-router"}).json()
    per = body["usage"]["per_agent"][aid]
    assert per["cost_usd"] == pytest.approx(0.01)
    assert per["served_model"] == "openai/gpt-4o"
    assert per["model"] == "typesafe/jev-router"


def test_served_model_omitted_when_same(client, monkeypatch):
    aid = _make_agent(client)

    async def _fake_complete(payload):
        return ({"ok": 1}, {"prompt_tokens": 5, "completion_tokens": 5,
                             "total_tokens": 10,
                             "served_model": "test-model"})

    async def _fake_pricing(model):
        return (None, None)

    monkeypatch.setattr(runs_router, "complete_json_payload", _fake_complete)
    monkeypatch.setattr(runs_router, "get_model_pricing", _fake_pricing)
    per = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [aid], "model": "test-model"}).json()["usage"]["per_agent"][aid]
    assert "served_model" not in per


def test_build_chat_payload_includes_usage():
    from app.core.openrouter import build_chat_payload
    p = build_chat_payload(model="m", system="s", user="u")
    assert p["usage"] == {"include": True}


def test_extract_usage_cost_and_details():
    u = openrouter._extract_usage({"prompt_tokens": 1, "completion_tokens": 2,
                                    "total_tokens": 3, "cost": 0.05,
                                    "cost_details": {"a": 1}})
    assert u["cost"] == pytest.approx(0.05)
    assert u["cost_details"] == {"a": 1}
    u2 = openrouter._extract_usage({"prompt_tokens": 1, "cost": "-1"})
    assert u2["cost"] is None
    u3 = openrouter._extract_usage({"prompt_tokens": 1, "cost": "oops"})
    assert u3["cost"] is None
