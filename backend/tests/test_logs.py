"""Coverage: log module list + detail (denormalized, no live FKs)."""


def test_logs_list_and_detail(client):
    aid = client.post("/api/v1/agents", json={"name": "L"}).json()["id"]
    rid = client.post("/api/v1/runs", json={
        "input_type": "transcription", "input_data": "hello",
        "agent_ids": [], "model": "m"}).json()["id"]

    logs = client.get("/api/v1/logs").json()
    assert len(logs) == 1
    lid = logs[0]["id"]
    assert logs[0]["run_id"] == rid

    detail = client.get(f"/api/v1/logs/{lid}").json()
    assert detail["input_data"] == "hello"
    assert detail["agent_snapshot"] == {}
    assert client.get("/api/v1/logs/missing").status_code == 404
