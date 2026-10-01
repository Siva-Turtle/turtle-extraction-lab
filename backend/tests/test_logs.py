"""Coverage: log module list + detail (denormalized, no live FKs)."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def _run(client, model="m", agent_ids=None):
    return client.post("/api/v1/runs", json={
        "input_type": "transcription", "input_data": "hello",
        "agent_ids": agent_ids or [], "model": model}).json()["id"]


def _ist_day(created_at: str) -> str:
    dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo("Asia/Kolkata")).date().isoformat()


def test_logs_list_and_detail(client):
    aid = client.post("/api/v1/agents", json={"name": "L"}).json()["id"]
    rid = _run(client)

    logs = client.get("/api/v1/logs").json()
    assert len(logs) == 1
    lid = logs[0]["id"]
    assert logs[0]["run_id"] == rid

    detail = client.get(f"/api/v1/logs/{lid}").json()
    assert detail["input_data"] == "hello"
    assert detail["agent_snapshot"] == {}
    assert client.get("/api/v1/logs/missing").status_code == 404


def test_logs_filter_by_model(client):
    _run(client, model="model-a")
    _run(client, model="model-b")

    assert len(client.get("/api/v1/logs").json()) == 2
    only_a = client.get("/api/v1/logs", params={"models": "model-a"}).json()
    assert len(only_a) == 1 and only_a[0]["model"] == "model-a"
    both = client.get(
        "/api/v1/logs", params=[("models", "model-a"), ("models", "model-b")]).json()
    assert len(both) == 2
    assert client.get("/api/v1/logs", params={"models": "nope"}).json() == []
    # axios-style bracket encoding is accepted too
    bracket = client.get("/api/v1/logs?models[]=model-a").json()
    assert len(bracket) == 1 and bracket[0]["model"] == "model-a"


def test_logs_filter_by_agent(client):
    a1 = client.post("/api/v1/agents", json={"name": "A1"}).json()["id"]
    a2 = client.post("/api/v1/agents", json={"name": "A2"}).json()["id"]
    _run(client, agent_ids=[a1])

    only = client.get("/api/v1/logs", params={"agent_ids": a1}).json()
    assert len(only) == 1 and a1 in only[0]["agent_snapshot"]
    assert client.get("/api/v1/logs", params={"agent_ids": a2}).json() == []
    both = client.get(
        "/api/v1/logs", params=[("agent_ids", a1), ("agent_ids", a2)]).json()
    assert len(both) == 1


def test_logs_filter_by_date(client):
    _run(client)
    logs = client.get("/api/v1/logs").json()
    assert len(logs) == 1
    day = _ist_day(logs[0]["created_at"])

    assert len(client.get("/api/v1/logs", params={"dates": day}).json()) == 1
    assert client.get("/api/v1/logs", params={"dates": "1999-01-01"}).json() == []


def test_logs_filters_combine_with_and(client):
    a1 = client.post("/api/v1/agents", json={"name": "B1"}).json()["id"]
    a2 = client.post("/api/v1/agents", json={"name": "B2"}).json()["id"]
    _run(client, model="model-a", agent_ids=[a1])
    _run(client, model="model-b", agent_ids=[a2])

    rows = client.get(
        "/api/v1/logs", params={"models": "model-a", "agent_ids": a1}).json()
    assert len(rows) == 1 and rows[0]["model"] == "model-a"
    rows = client.get(
        "/api/v1/logs", params={"models": "model-a", "agent_ids": a2}).json()
    assert rows == []
