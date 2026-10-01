"""CRUD coverage: attributes config module (many-to-many agents, enum types)."""


def _agent_id(client, name="Attr Agent"):
    return client.post("/api/v1/agents", json={"name": name}).json()["id"]


def test_attribute_multi_agent_crud(client):
    aid = _agent_id(client)
    aid2 = _agent_id(client, "Other")
    created = client.post("/api/v1/attributes", json={
        "agent_ids": [aid, aid2], "name": "email", "type": "string",
        "description": "Email if mentioned",
        "enum_values": ["ignored-for-string"]}).json()
    assert set(created["agent_ids"]) == {aid, aid2}
    assert created["enum_values"] == []  # non-enum never stores options
    assert "json_schema" not in created and "required" not in created
    assert "agent_id" not in created
    atid = created["id"]

    assert client.get(f"/api/v1/attributes/{atid}").json()["name"] == "email"
    assert client.post("/api/v1/attributes", json={
        "agent_ids": ["missing"], "name": "x"}).status_code == 404

    # list + agent filter via the association join
    client.post("/api/v1/attributes", json={"agent_ids": [aid2], "name": "phone"})
    all_rows = client.get("/api/v1/attributes").json()
    assert len(all_rows) == 2
    assert [r["name"] for r in client.get("/api/v1/attributes", params={"agent_id": aid}).json()] == ["email"]

    # re-link to a single agent
    patched = client.patch(f"/api/v1/attributes/{atid}", json={"agent_ids": [aid2]}).json()
    assert patched["agent_ids"] == [aid2]
    assert client.get("/api/v1/attributes", params={"agent_id": aid}).json() == []
    assert client.patch(f"/api/v1/attributes/{atid}", json={"agent_ids": ["missing"]}).status_code == 404
    assert client.patch(f"/api/v1/attributes/{atid}", json={"agent_ids": []}).status_code == 422

    assert client.delete(f"/api/v1/attributes/{atid}").json() == {"ok": True}
    assert client.get(f"/api/v1/attributes/{atid}").status_code == 404


def test_attribute_type_validation(client):
    aid = _agent_id(client)

    # bad type -> 422
    assert client.post("/api/v1/attributes", json={
        "agent_ids": [aid], "name": "x", "type": "date"}).status_code == 422

    # enum with empty enum_values -> 422
    assert client.post("/api/v1/attributes", json={
        "agent_ids": [aid], "name": "mood", "type": "enum"}).status_code == 422
    assert client.post("/api/v1/attributes", json={
        "agent_ids": [aid], "name": "mood", "type": "enum",
        "enum_values": []}).status_code == 422

    # enum with options works
    created = client.post("/api/v1/attributes", json={
        "agent_ids": [aid], "name": "mood", "type": "enum",
        "enum_values": ["good", "bad"]}).json()
    assert created["enum_values"] == ["good", "bad"]

    # empty agent_ids -> 422 (min_length=1)
    assert client.post("/api/v1/attributes", json={
        "agent_ids": [], "name": "x"}).status_code == 422

    # patch to an invalid type -> 422; flipping enum -> string clears options
    atid = created["id"]
    assert client.patch(f"/api/v1/attributes/{atid}", json={"type": "date"}).status_code == 422
    flipped = client.patch(f"/api/v1/attributes/{atid}", json={"type": "string"}).json()
    assert flipped["type"] == "string" and flipped["enum_values"] == []
