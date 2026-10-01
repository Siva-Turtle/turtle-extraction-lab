"""Coverage: test runs (OpenRouter stubbed), feedback, denormalized log snapshots."""

import app.modules.runs.router as runs_router


async def _fake_complete(*, model, system, user):
    assert model == "test-model"
    return ({"email": {"value": "a@b.in", "confidence": 0.9,
                      "confidence_type": "quoted", "evidence": "mail me at a@b.in"}},
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})


def _setup(client, monkeypatch):
    monkeypatch.setattr(runs_router, "complete_json", _fake_complete)
    aid = client.post("/api/v1/agents", json={"name": "A", "prompt": "p"}).json()["id"]
    client.post("/api/v1/attributes", json={"agent_id": aid, "name": "email"})
    return aid


def test_run_and_feedback_flow(client, monkeypatch):
    aid = _setup(client, monkeypatch)
    run = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": [aid], "model": "test-model"}).json()
    assert run["outputs"][aid]["email"]["value"] == "a@b.in"
    rid = run["id"]

    assert client.post("/api/v1/runs", json={
        "input_type": "sms", "input_data": "x",
        "agent_ids": [aid], "model": "m"}).status_code == 422

    detail = client.get(f"/api/v1/runs/{rid}").json()
    assert detail["input_data"] == "mail me at a@b.in"
    assert client.get("/api/v1/runs/missing").status_code == 404

    fb = client.post(f"/api/v1/runs/{rid}/feedback", json={
        "agent_name": "A", "attribute_name": "email",
        "rating": "up", "remarks": "exact quote"}).json()
    assert fb == {"ok": True}
    assert client.post(f"/api/v1/runs/{rid}/feedback",
                       json={"rating": "meh"}).status_code == 422
    got = client.get(f"/api/v1/runs/{rid}/feedback").json()
    assert [(f["rating"], f["remarks"]) for f in got] == [("up", "exact quote")]

    # Log row carries frozen snapshots + merged feedback.
    logs = client.get("/api/v1/logs").json()
    assert len(logs) == 1
    assert logs[0]["run_id"] == rid
    assert logs[0]["agent_snapshot"][aid]["name"] == "A"
    assert logs[0]["feedback"] == {"A": {"email": {"rating": "up", "remarks": "exact quote"}}}


def test_run_without_key_records_error(client):
    aid = client.post("/api/v1/agents", json={"name": "B"}).json()["id"]
    run = client.post("/api/v1/runs", json={
        "input_type": "messages", "input_data": "hi",
        "agent_ids": [aid], "model": "m"}).json()
    assert "_error" in run["outputs"][aid]  # no OPENROUTER_API_KEY in test env
