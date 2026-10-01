"""CRUD coverage: agents config module."""


def _agent(name="Extractor A"):
    return {"name": name, "system_instruction": "sys", "prompt": "do it",
            "input_types": ["transcription", "mail"]}


def test_agent_crud(client):
    created = client.post("/api/v1/agents", json=_agent()).json()
    assert created["name"] == "Extractor A"
    aid = created["id"]

    assert client.get(f"/api/v1/agents/{aid}").json()["prompt"] == "do it"
    assert client.get("/api/v1/agents/does-not-exist").status_code == 404

    patched = client.patch(f"/api/v1/agents/{aid}", json={"prompt": "do it better"}).json()
    assert patched["prompt"] == "do it better"
    assert patched["name"] == "Extractor A"  # untouched fields stay
    assert client.patch("/api/v1/agents/does-not-exist", json={"prompt": "x"}).status_code == 404

    assert any(a["id"] == aid for a in client.get("/api/v1/agents").json())

    assert client.delete(f"/api/v1/agents/{aid}").json() == {"ok": True}
    assert client.get(f"/api/v1/agents/{aid}").status_code == 404
    assert client.delete(f"/api/v1/agents/{aid}").status_code == 404


def test_delete_agent_cascades_attributes(client):
    aid = client.post("/api/v1/agents", json=_agent()).json()["id"]
    attr = client.post("/api/v1/attributes", json={
        "agent_id": aid, "name": "phone", "type": "string"}).json()
    client.delete(f"/api/v1/agents/{aid}")
    assert client.get(f"/api/v1/attributes/{attr['id']}").status_code == 404
