"""Coverage: instance status + model listing (no database needed)."""

import asyncio

import app.core.openrouter as orouter
from app.core.config import settings


async def _fake_models():
    return orouter.CURATED_MODELS, False


def test_config_shape(client):
    body = client.get("/api/v1/config").json()
    assert set(body) == {"openrouter_configured", "db_ok"}
    assert isinstance(body["openrouter_configured"], bool)
    # db_ok reflects the real engine reachability (env-dependent); just check shape.
    assert isinstance(body["db_ok"], bool)


def test_models_fallback(client, monkeypatch):
    monkeypatch.setattr(orouter, "fetch_models", _fake_models)
    body = client.get("/api/v1/models").json()
    assert body["live"] is False
    assert any(m["id"] == "anthropic/claude-sonnet-4" for m in body["models"])


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _fake_client_factory(payload):
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            return _FakeResp(payload)

    return _FakeClient


def test_fetch_models_reasoning_passthrough(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    payload = {"data": [
        {"id": "b-model", "name": "B",
         "reasoning": {"supported_efforts": ["high", "medium", "low"],
                       "default_effort": "medium", "default_enabled": True,
                       "mandatory": False}},
        {"id": "a-model",
         "pricing": {"prompt": "0.000001", "completion": "0.000002"},
         "reasoning": {"supported_efforts": None}},
        {"id": "c-model"},
    ]}
    monkeypatch.setattr(orouter.httpx, "AsyncClient", _fake_client_factory(payload))
    models, live = asyncio.run(orouter.fetch_models())
    assert live is True
    assert [m["id"] for m in models] == ["a-model", "b-model", "c-model"]
    assert models[1]["reasoning"] == {
        "supported_efforts": ["high", "medium", "low"],
        "default_effort": "medium", "default_enabled": True, "mandatory": False}
    assert models[0]["reasoning"] == {"supported_efforts": None}
    assert models[0]["name"] == "a-model"
    assert models[2]["reasoning"] is None
    assert models[2]["name"] == "c-model"


def test_model_entry_pricing_parsing():
    assert orouter._model_entry({
        "id": "m", "name": "M",
        "pricing": {"prompt": "0.000001", "completion": "0.000002"},
    })["pricing"] == {"prompt": 0.000001, "completion": 0.000002}
    # Missing pricing block -> Nones; malformed strings -> None, never raises.
    assert orouter._model_entry({"id": "m"})["pricing"] == {
        "prompt": None, "completion": None}
    assert orouter._model_entry({
        "id": "m", "pricing": {"prompt": "oops", "completion": None},
    })["pricing"] == {"prompt": None, "completion": None}
    # Fallback entries carry pricing None.
    old = settings.openrouter_api_key
    try:
        settings.openrouter_api_key = ""
        fb_models, fb_live = asyncio.run(orouter.fetch_models())
        assert fb_live is False
        assert all(m["pricing"] is None for m in fb_models)
    finally:
        settings.openrouter_api_key = old
