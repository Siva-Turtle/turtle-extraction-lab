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


def test_attribute_array_type(client):
    aid = _agent_id(client)

    # array needs no extra config; stray enum/object config is dropped
    created = client.post("/api/v1/attributes", json={
        "agent_ids": [aid], "name": "emails", "type": "array",
        "description": "Email addresses mentioned",
        "enum_values": ["ignored"],
        "object_properties": [{"name": "x", "type": "string",
                               "null_allowed": True}]}).json()
    assert created["type"] == "array"
    assert created["enum_values"] == []
    assert created["object_properties"] == []


def test_attribute_object_validation(client):
    aid = _agent_id(client)

    # missing / empty object_properties -> 422
    assert client.post("/api/v1/attributes", json={
        "agent_ids": [aid], "name": "alloc", "type": "object"}).status_code == 422
    assert client.post("/api/v1/attributes", json={
        "agent_ids": [aid], "name": "alloc", "type": "object",
        "object_properties": []}).status_code == 422

    # blank name, bad sub-type, dupes -> 422
    # (non-bool null_allowed never reaches the router: pydantic coerces
    # truthy strings to bool before validation runs)
    bad_props = [
        [{"name": " ", "type": "number", "null_allowed": True}],
        [{"name": "x", "type": "date", "null_allowed": True}],
        [{"name": "a", "type": "number", "null_allowed": True},
         {"name": "a", "type": "string", "null_allowed": True}],
    ]
    for props in bad_props:
        assert client.post("/api/v1/attributes", json={
            "agent_ids": [aid], "name": "alloc", "type": "object",
            "object_properties": props}).status_code == 422

    # valid object round-trips ordered sub-fields, clears enum_values
    created = client.post("/api/v1/attributes", json={
        "agent_ids": [aid], "name": "alloc", "type": "object",
        "description": "Asset allocation",
        "enum_values": ["ignored"],
        "object_properties": [
            {"name": "equity", "type": "number", "null_allowed": True},
            {"name": "debt", "type": "number", "null_allowed": False},
            {"name": "tags", "type": "array", "null_allowed": True},
        ]}).json()
    assert created["enum_values"] == []
    assert created["object_properties"] == [
        {"name": "equity", "type": "number", "null_allowed": True},
        {"name": "debt", "type": "number", "null_allowed": False},
        {"name": "tags", "type": "array", "null_allowed": True},
    ]
    atid = created["id"]

    # flipping object -> array clears sub-fields
    flipped = client.patch(f"/api/v1/attributes/{atid}", json={"type": "array"}).json()
    assert flipped["type"] == "array" and flipped["object_properties"] == []

    # flipping back to object with fresh props works
    back = client.patch(f"/api/v1/attributes/{atid}", json={
        "type": "object",
        "object_properties": [{"name": "gold", "type": "number",
                               "null_allowed": True}]}).json()
    assert back["object_properties"] == [
        {"name": "gold", "type": "number", "null_allowed": True}]
