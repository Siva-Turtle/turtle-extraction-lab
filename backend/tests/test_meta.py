"""Coverage: instance status + model listing (no database needed)."""

import app.core.openrouter as orouter


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
