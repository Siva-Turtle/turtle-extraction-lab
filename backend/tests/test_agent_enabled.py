"""Agent On/Off toggle round-trip."""


def test_agent_enabled_toggle_roundtrip(client):
    created = client.post("/api/v1/agents", json={"name": "Toggle Me"}).json()
    assert created["is_enabled"] is True

    aid = created["id"]
    assert client.get(f"/api/v1/agents/{aid}").json()["is_enabled"] is True

    patched = client.patch(f"/api/v1/agents/{aid}", json={"is_enabled": False}).json()
    assert patched["is_enabled"] is False

    listed = {a["id"]: a for a in client.get("/api/v1/agents").json()}
    assert listed[aid]["is_enabled"] is False

    detail = client.get(f"/api/v1/agents/{aid}").json()
    assert detail["is_enabled"] is False

    back_on = client.patch(f"/api/v1/agents/{aid}", json={"is_enabled": True}).json()
    assert back_on["is_enabled"] is True
