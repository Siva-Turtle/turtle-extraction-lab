"""Reused outputs share ONE feedback source of truth."""

import app.modules.runs.router as runs_router


def _make_agent(client, name="R"):
    aid = client.post("/api/v1/agents", json={"name": name}).json()["id"]
    client.post("/api/v1/attributes", json={"agent_ids": [aid], "name": "email"})
    return aid


async def _ok(payload):
    return ({"email": {"value": "x", "confidence": 1.0,
                       "confidence_type": "quoted", "evidence": "e"}},
            {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10})


async def _no_pricing(model):
    return (None, None)


def _run(client, monkeypatch, aids, model="m", reuse=None):
    body = {"input_type": "mail", "input_data": "hello",
            "agent_ids": aids, "model": model}
    if reuse:
        body["reuse"] = reuse
    return client.post("/api/v1/runs", json=body).json()


def test_rating_reused_cell_updates_source_only(client, monkeypatch):
    aid = _make_agent(client, "RA")
    monkeypatch.setattr(runs_router, "complete_json_payload", _ok)
    monkeypatch.setattr(runs_router, "get_model_pricing", _no_pricing)
    first = _run(client, monkeypatch, [aid])
    src_log_id = first["log_id"]
    src_run_id = first["id"]

    second = _run(client, monkeypatch, [aid], reuse={aid: src_log_id})
    new_run_id = second["id"]
    new_log_id = second["log_id"]
    assert second["usage"]["reused_agents"][aid]["log_id"] == src_log_id

    # Rate the REUSED cell via the new run.
    r = client.post("/api/v1/runs/feedback-batch", json={
        "items": [{"run_id": new_run_id, "agent_name": "RA",
                   "attribute_name": "email", "rating": "up"}]})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["applied"] == 1
    # Backend resolves to the source run.
    resolved = data.get("resolved", [])
    assert resolved and resolved[0]["resolved_run_id"] == src_run_id

    # Source DB row holds the rating; new row holds no copy.
    from app.db.session import Base  # noqa (ensure models loaded)
    # Fetch via logs API (merged view) + direct DB check via API? Use logs.
    src_log = client.get(f"/api/v1/logs/{src_log_id}").json()
    assert src_log["feedback"]["RA"]["email"]["rating"] == "up"
    new_log = client.get(f"/api/v1/logs/{new_log_id}").json()
    # Merged display shows source feedback with marker...
    assert new_log["feedback"]["RA"]["email"]["rating"] == "up"
    assert new_log["feedback"]["RA"]["email"]["source_log_id"] == src_log_id
    # ...but the DB row itself holds no copy (checked via internal endpoint?
    # logs API merges, so check that a legacy copy would be ignored: write a
    # legacy copy directly and ensure display still prefers source).
    # Non-reused cells are unchanged (covered below).


def test_log_group_returns_source_feedback(client, monkeypatch):
    aid = _make_agent(client, "RB")
    monkeypatch.setattr(runs_router, "complete_json_payload", _ok)
    monkeypatch.setattr(runs_router, "get_model_pricing", _no_pricing)
    first = _run(client, monkeypatch, [aid])
    src_log_id = first["log_id"]
    src_run_id = first["id"]
    # Rate the SOURCE directly.
    client.post(f"/api/v1/runs/{src_run_id}/feedback", json={
        "agent_name": "RB", "attribute_name": "email",
        "rating": "down", "remarks": "bad"})
    second = _run(client, monkeypatch, [aid], reuse={aid: src_log_id})
    new_log_id = second["log_id"]
    # Log-group fetch (by run_group or by id) merges source feedback.
    grp = second.get("run_group_id", "") or new_log_id
    # Use the single-log fetch which shares the merge path.
    merged = client.get(f"/api/v1/logs/{new_log_id}").json()
    assert merged["feedback"]["RB"]["email"]["rating"] == "down"
    assert merged["feedback"]["RB"]["email"]["remarks"] == "bad"
    assert merged["feedback"]["RB"]["email"]["source_log_id"] == src_log_id


def test_chain_resolution_and_cycle_guard(client, monkeypatch, db):
    from app.db.models import RunLog
    aid = _make_agent(client, "RC")
    monkeypatch.setattr(runs_router, "complete_json_payload", _ok)
    monkeypatch.setattr(runs_router, "get_model_pricing", _no_pricing)
    first = _run(client, monkeypatch, [aid])
    second = _run(client, monkeypatch, [aid], reuse={aid: first["log_id"]})
    third = _run(client, monkeypatch, [aid], reuse={aid: second["log_id"]})
    # Chain: third -> second -> first. Rating third must land on first.
    r = client.post("/api/v1/runs/feedback-batch", json={
        "items": [{"run_id": third["id"], "agent_name": "RC",
                   "attribute_name": "email", "rating": "up"}]})
    assert r.status_code == 200, r.text
    assert r.json()["resolved"][0]["resolved_run_id"] == first["id"]
    # Original holds it; middle holds no copy.
    first_log = db.query(RunLog).filter(RunLog.id == first["log_id"]).first()
    second_log = db.query(RunLog).filter(RunLog.id == second["log_id"]).first()
    third_log = db.query(RunLog).filter(RunLog.id == third["log_id"]).first()
    assert first_log.feedback["RC"]["email"]["rating"] == "up"
    assert "RC" not in (third_log.feedback or {})
    # Middle display merges from original.
    merged = client.get(f"/api/v1/logs/{second['log_id']}").json()
    assert merged["feedback"]["RC"]["email"]["rating"] == "up"


def test_non_reused_cells_unchanged(client, monkeypatch, db):
    from app.db.models import RunLog
    aid1 = _make_agent(client, "RD1")
    aid2 = _make_agent(client, "RD2")
    monkeypatch.setattr(runs_router, "complete_json_payload", _ok)
    monkeypatch.setattr(runs_router, "get_model_pricing", _no_pricing)
    first = _run(client, monkeypatch, [aid1, aid2])
    second = _run(client, monkeypatch, [aid1, aid2],
                  reuse={aid1: first["log_id"]})
    # Rate the FRESH agent (aid2) on the new run: stays on the new row.
    r = client.post("/api/v1/runs/feedback-batch", json={
        "items": [{"run_id": second["id"], "agent_name": "RD2",
                   "attribute_name": "email", "rating": "down"}]})
    assert r.status_code == 200
    assert r.json()["resolved"][0]["resolved_run_id"] == second["id"]
    new_row = db.query(RunLog).filter(RunLog.id == second["log_id"]).first()
    assert new_row.feedback["RD2"]["email"]["rating"] == "down"
    old_row = db.query(RunLog).filter(RunLog.id == first["log_id"]).first()
    assert "RD2" not in (old_row.feedback or {})


def test_legacy_copy_ignored_in_favour_of_source(client, monkeypatch, db):
    from app.db.models import RunLog
    from sqlalchemy.orm.attributes import flag_modified
    aid = _make_agent(client, "RE")
    monkeypatch.setattr(runs_router, "complete_json_payload", _ok)
    monkeypatch.setattr(runs_router, "get_model_pricing", _no_pricing)
    first = _run(client, monkeypatch, [aid])
    client.post(f"/api/v1/runs/{first['id']}/feedback", json={
        "agent_name": "RE", "attribute_name": "email",
        "rating": "up", "remarks": "src"})
    second = _run(client, monkeypatch, [aid], reuse={aid: first["log_id"]})
    # Legacy: new row already holds its own (stale) copy.
    row = db.query(RunLog).filter(RunLog.id == second["log_id"]).first()
    row.feedback = {"RE": {"email": {"rating": "down", "remarks": "stale"}}}
    flag_modified(row, "feedback")
    db.commit()
    merged = client.get(f"/api/v1/logs/{second['log_id']}").json()
    assert merged["feedback"]["RE"]["email"]["rating"] == "up"
    assert merged["feedback"]["RE"]["email"]["remarks"] == "src"
