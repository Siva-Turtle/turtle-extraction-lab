"""CRUD coverage: agents config module (prompt dropped from the API)."""

from app.modules.runs.router import _agent_system_content, build_extraction_schema


def _agent(name="Extractor A"):
    return {"name": name, "system_instruction": "sys",
            "input_types": ["transcription", "mail"]}


def _attr(agent_ids, name="field", attr_type="string", **kw):
    body = {"agent_ids": agent_ids, "name": name, "type": attr_type}
    body.update(kw)
    return body


def test_agent_crud(client):
    created = client.post("/api/v1/agents", json=_agent()).json()
    assert created["name"] == "Extractor A"
    assert "prompt" not in created  # dead field stays out of the contract
    assert created["description"] == ""  # defaults
    assert created["kind"] == "extraction"
    aid = created["id"]

    detail = client.get(f"/api/v1/agents/{aid}").json()
    assert "prompt" not in detail
    assert detail["system_instruction"] == "sys"
    assert client.get("/api/v1/agents/does-not-exist").status_code == 404

    patched = client.patch(f"/api/v1/agents/{aid}", json={"system_instruction": "sys v2"}).json()
    assert patched["system_instruction"] == "sys v2"
    assert patched["name"] == "Extractor A"  # untouched fields stay
    assert patched["description"] == "" and patched["kind"] == "extraction"

    described = client.patch(
        f"/api/v1/agents/{aid}",
        json={"description": "Routes transcripts.", "kind": "identifier"}).json()
    assert described["description"] == "Routes transcripts."
    assert described["kind"] == "identifier"
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


def _linked_names(client, agent_id):
    return sorted(r["name"] for r in
                  client.get("/api/v1/attributes", params={"agent_id": agent_id}).json())


def test_patch_agent_attribute_ids_replace(client):
    aid = client.post("/api/v1/agents", json=_agent("Linker")).json()["id"]
    a = client.post("/api/v1/attributes", json=_attr([aid], "a")).json()
    b = client.post("/api/v1/attributes", json=_attr([aid], "b")).json()
    assert _linked_names(client, aid) == ["a", "b"]

    patched = client.patch(f"/api/v1/agents/{aid}", json={"attribute_ids": [b["id"]]})
    assert patched.status_code == 200
    # Replace semantics: a is unlinked (but not deleted), only b remains...
    assert _linked_names(client, aid) == ["b"]
    assert client.get(f"/api/v1/attributes/{a['id']}").status_code == 200
    # ...and AgentOut shape is unchanged (modulo description/kind).
    assert set(patched.json()) == {"id", "name", "description", "kind",
                                   "system_instruction", "input_types",
                                   "is_enabled"}

    # Empty list clears all links without deleting the attributes.
    assert client.patch(f"/api/v1/agents/{aid}", json={"attribute_ids": []}).status_code == 200
    assert _linked_names(client, aid) == []
    assert client.get(f"/api/v1/attributes/{a['id']}").status_code == 200
    assert client.get(f"/api/v1/attributes/{b['id']}").status_code == 200


def test_patch_agent_attribute_ids_unknown_404(client):
    aid = client.post("/api/v1/agents", json=_agent("Linker2")).json()["id"]
    a = client.post("/api/v1/attributes", json=_attr([aid], "keep")).json()
    resp = client.patch(f"/api/v1/agents/{aid}", json={"attribute_ids": ["nope"]})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "attribute not found"
    # Failed validation leaves existing links untouched.
    assert _linked_names(client, aid) == ["keep"]
    assert client.patch("/api/v1/agents/does-not-exist",
                        json={"attribute_ids": [a["id"]]}).status_code == 404


def test_prompt_preview_fidelity(client, db):
    from app.core.openrouter import build_chat_payload
    from app.db.models import Agent
    from app.modules.runs.router import _attrs_for_agent

    aid = client.post("/api/v1/agents",
                      json=_agent("Previewed")).json()["id"]
    client.post("/api/v1/attributes", json=_attr([aid], "topic")).json()
    client.post("/api/v1/attributes", json=_attr(
        [aid], "sentiment", "enum", enum_values=["pos", "neg"],
        description="call mood")).json()

    preview = client.get(f"/api/v1/agents/{aid}/prompt-preview")
    assert preview.status_code == 200
    body = preview.json()
    assert set(body) == {"agent_id", "system", "user_template",
                         "response_format", "attributes"}
    assert body["agent_id"] == aid
    assert body["user_template"] == "<transcription — pulled automatically on run>"

    # Byte-identical to what a run would send: same builders, same inputs.
    agent = db.query(Agent).filter(Agent.id == aid).first()
    attrs = _attrs_for_agent(db, aid)
    assert body["system"] == _agent_system_content(agent, attrs)
    schema = build_extraction_schema(attrs)
    assert schema is not None
    expected_format = build_chat_payload(
        model="probe", system=body["system"], user="probe",
        json_schema=schema)["response_format"]
    assert body["response_format"] == expected_format
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "meeting_extraction", "strict": True, "schema": schema},
    }
    assert body["attributes"] == [
        {"name": a.name, "type": a.type, "description": a.description,
         "group": getattr(a, "group_name", "") or "",
         "enum_values": a.enum_values or [],
         "object_properties": list(getattr(a, "object_properties", None) or []),
         "array_items": dict(getattr(a, "array_items", None) or
                             {"kind": "string", "properties": []}),
         "wrap_result": (getattr(a, "wrap_result", True) is not False)} for a in attrs]
    assert {a["name"] for a in body["attributes"]} == {"topic", "sentiment"}

    assert client.get("/api/v1/agents/does-not-exist/prompt-preview").status_code == 404


def test_prompt_preview_no_attributes_fallback(client):
    aid = client.post("/api/v1/agents", json=_agent("Bare")).json()["id"]
    body = client.get(f"/api/v1/agents/{aid}/prompt-preview").json()
    assert body["attributes"] == []
    assert body["response_format"] == {"type": "json_object"}  # runs fallback
    assert body["user_template"] == "<transcription — pulled automatically on run>"
    assert "(no attributes defined)" in body["system"]
