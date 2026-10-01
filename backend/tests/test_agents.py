"""CRUD coverage: agents config module (prompt dropped from the API)."""


def _agent(name="Extractor A"):
    return {"name": name, "system_instruction": "sys",
            "input_types": ["transcription", "mail"]}


def test_agent_crud(client):
    created = client.post("/api/v1/agents", json=_agent()).json()
    assert created["name"] == "Extractor A"
    assert "prompt" not in created  # dead field stays out of the contract
    aid = created["id"]

    detail = client.get(f"/api/v1/agents/{aid}").json()
    assert "prompt" not in detail
    assert detail["system_instruction"] == "sys"
    assert client.get("/api/v1/agents/does-not-exist").status_code == 404

    patched = client.patch(f"/api/v1/agents/{aid}", json={"system_instruction": "sys v2"}).json()
    assert patched["system_instruction"] == "sys v2"
    assert patched["name"] == "Extractor A"  # untouched fields stay
    assert client.patch("/api/v1/agents/does-not-exist", json={"name": "x"}).status_code == 404

    assert any(a["id"] == aid for a in client.get("/api/v1/agents").json())

    assert client.delete(f"/api/v1/agents/{aid}").json() == {"ok": True}
    assert client.get(f"/api/v1/agents/{aid}").status_code == 404
    assert client.delete(f"/api/v1/agents/{aid}").status_code == 404


def test_agent_legacy_prompt_row_unaffected(client, db):
    """Rows written before the remodel (prompt column populated) still load —
    the dormant column is ignored, never exposed."""
    from app.db.models import Agent
    db.add(Agent(name="Legacy", system_instruction="s", prompt="old prompt text"))
    db.commit()
    rows = client.get("/api/v1/agents").json()
    legacy = next(a for a in rows if a["name"] == "Legacy")
    assert "prompt" not in legacy
    assert legacy["system_instruction"] == "s"


def test_delete_agent_orphan_cleanup_and_shared_survive(client):
    aid1 = client.post("/api/v1/agents", json=_agent("A1")).json()["id"]
    aid2 = client.post("/api/v1/agents", json=_agent("A2")).json()["id"]
    solo = client.post("/api/v1/attributes", json={
        "agent_ids": [aid1], "name": "solo", "type": "string"}).json()
    shared = client.post("/api/v1/attributes", json={
        "agent_ids": [aid1, aid2], "name": "shared", "type": "string"}).json()

    client.delete(f"/api/v1/agents/{aid1}")

    # Orphaned attribute is gone with its agent...
    assert client.get(f"/api/v1/attributes/{solo['id']}").status_code == 404
    # ...but the shared attribute survives, now linked only to A2.
    kept = client.get(f"/api/v1/attributes/{shared['id']}").json()
    assert kept["agent_ids"] == [aid2]
    assert [r["name"] for r in
            client.get("/api/v1/attributes", params={"agent_id": aid2}).json()] == ["shared"]
    assert client.get("/api/v1/attributes", params={"agent_id": aid1}).json() == []
