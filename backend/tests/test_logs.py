"""Coverage: log module list + detail (denormalized, no live FKs)."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def _run(client, model="m", agent_ids=None):
    return client.post("/api/v1/runs", json={
        "input_type": "transcription", "input_data": "hello",
        "agent_ids": agent_ids or [], "model": model}).json()["id"]


def _run_with_meeting(client, model="m", client_name="", meeting_type="", meeting_title=""):
    return client.post("/api/v1/runs", json={
        "input_type": "transcription", "input_data": "hello",
        "agent_ids": [], "model": model,
        "client": client_name, "meeting_type": meeting_type,
        "meeting_title": meeting_title}).json()["id"]


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


def test_logs_meeting_fields_stored_and_default_empty(client):
    _run_with_meeting(client, client_name="Acme", meeting_type="Tax Review",
                      meeting_title="Tax Review — 01 Jan")

    logs = client.get("/api/v1/logs").json()
    assert len(logs) == 1
    assert logs[0]["client"] == "Acme"
    assert logs[0]["meeting_type"] == "Tax Review"
    assert logs[0]["meeting_title"] == "Tax Review — 01 Jan"
    detail = client.get(f"/api/v1/logs/{logs[0]['id']}").json()
    assert detail["client"] == "Acme"
    assert detail["meeting_type"] == "Tax Review"
    assert detail["meeting_title"] == "Tax Review — 01 Jan"

    # Old rows (created before the fields existed) carry "" — never crash.
    _run(client)
    logs = client.get("/api/v1/logs").json()
    assert len(logs) == 2
    legacy = [l for l in logs if l["client"] == ""]
    assert len(legacy) == 1
    assert legacy[0]["meeting_type"] == "" and legacy[0]["meeting_title"] == ""


def test_logs_filter_by_client(client):
    _run_with_meeting(client, client_name="Acme")
    _run_with_meeting(client, client_name="Globex")
    _run(client)  # legacy row with "" client

    assert len(client.get("/api/v1/logs").json()) == 3
    only = client.get("/api/v1/logs", params={"clients": "Acme"}).json()
    assert len(only) == 1 and only[0]["client"] == "Acme"
    both = client.get(
        "/api/v1/logs", params=[("clients", "Acme"), ("clients", "Globex")]).json()
    assert len(both) == 2
    assert client.get("/api/v1/logs", params={"clients": "nope"}).json() == []
    # Blank filter values are ignored (legacy "" rows are never matched by name).
    assert len(client.get("/api/v1/logs", params={"clients": "  "}).json()) == 3
    # axios-style bracket encoding is accepted too
    bracket = client.get("/api/v1/logs?clients[]=Acme").json()
    assert len(bracket) == 1 and bracket[0]["client"] == "Acme"


def test_logs_filter_by_meeting_type_and_title(client):
    _run_with_meeting(client, meeting_type="Tax Review", meeting_title="Tax Review — 01 Jan")
    _run_with_meeting(client, meeting_type="Karma", meeting_title="Karma — 02 Jan")
    _run(client)  # legacy row with "" fields

    only_type = client.get("/api/v1/logs", params={"meeting_types": "Tax Review"}).json()
    assert len(only_type) == 1 and only_type[0]["meeting_type"] == "Tax Review"
    both_types = client.get(
        "/api/v1/logs",
        params=[("meeting_types", "Tax Review"), ("meeting_types", "Karma")]).json()
    assert len(both_types) == 2
    assert client.get("/api/v1/logs", params={"meeting_types": "nope"}).json() == []

    only_title = client.get(
        "/api/v1/logs", params={"meeting_titles": "Karma — 02 Jan"}).json()
    assert len(only_title) == 1 and only_title[0]["meeting_title"] == "Karma — 02 Jan"
    assert client.get("/api/v1/logs", params={"meeting_titles": "nope"}).json() == []
    bracket = client.get("/api/v1/logs?meeting_titles[]=Karma — 02 Jan").json()
    assert len(bracket) == 1


def test_logs_meeting_filters_combine_with_and(client):
    _run_with_meeting(client, model="model-a", client_name="Acme",
                      meeting_type="Tax Review", meeting_title="Tax Review — 01 Jan")
    _run_with_meeting(client, model="model-b", client_name="Acme",
                      meeting_type="Karma", meeting_title="Karma — 02 Jan")

    rows = client.get(
        "/api/v1/logs", params={"clients": "Acme", "meeting_types": "Tax Review"}).json()
    assert len(rows) == 1 and rows[0]["meeting_title"] == "Tax Review — 01 Jan"
    rows = client.get(
        "/api/v1/logs",
        params={"clients": "Acme", "meeting_types": "Tax Review",
                "meeting_titles": "Karma — 02 Jan"}).json()
    assert rows == []
    rows = client.get(
        "/api/v1/logs", params={"models": "model-b", "clients": "Acme"}).json()
    assert len(rows) == 1 and rows[0]["model"] == "model-b"


def test_logs_legacy_row_without_meeting_kwargs(client, db):
    from app.db.models import RunLog
    # Old rows constructed before the columns existed carry no meeting kwargs.
    row = RunLog(run_id="legacy-run", input_type="mail", input_data="hi",
                 model="m", agent_snapshot={}, attribute_snapshot={},
                 outputs={}, feedback={}, usage={}, filters={})
    assert (row.client or "") == ""
    assert (row.meeting_type or "") == ""
    assert (row.meeting_title or "") == ""
    db.add(row)
    db.commit()

    logs = client.get("/api/v1/logs").json()
    assert len(logs) == 1
    assert logs[0]["client"] == ""
    assert logs[0]["meeting_type"] == ""
    assert logs[0]["meeting_title"] == ""
    # Legacy "" rows never match a named filter, but unfiltered lists keep them.
    assert client.get("/api/v1/logs", params={"clients": "Acme"}).json() == []
