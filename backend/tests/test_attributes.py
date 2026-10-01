"""CRUD coverage: attributes config module."""


def _agent_id(client, name="Attr Agent"):
    return client.post("/api/v1/agents", json={"name": name}).json()["id"]


def test_attribute_crud(client):
    aid = _agent_id(client)
    created = client.post("/api/v1/attributes", json={
        "agent_id": aid, "name": "email", "type": "string",
        "description": "Email if mentioned", "json_schema": {"format": "email"},
        "required": True}).json()
    assert created["json_schema"] == {"format": "email"}
    atid = created["id"]

    assert client.get(f"/api/v1/attributes/{atid}").json()["name"] == "email"
    assert client.post("/api/v1/attributes", json={
        "agent_id": "missing", "name": "x"}).status_code == 404

    # list + agent filter
    aid2 = _agent_id(client, "Other")
    client.post("/api/v1/attributes", json={"agent_id": aid2, "name": "phone"})
    all_rows = client.get("/api/v1/attributes").json()
    assert len(all_rows) == 2
    assert [r["name"] for r in client.get("/api/v1/attributes", params={"agent_id": aid}).json()] == ["email"]

    patched = client.patch(f"/api/v1/attributes/{atid}", json={"required": False}).json()
    assert patched["required"] is False
    assert client.patch(f"/api/v1/attributes/{atid}", json={"agent_id": "missing"}).status_code == 404

    assert client.delete(f"/api/v1/attributes/{atid}").json() == {"ok": True}
    assert client.get(f"/api/v1/attributes/{atid}").status_code == 404
