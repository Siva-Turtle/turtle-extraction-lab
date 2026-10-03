"""Streaming NDJSON mode: POST /runs and POST /runs/auto with stream=true."""

import asyncio
import json
import time

import app.modules.runs.router as runs_router


def _parse_ndjson(resp):
    text = resp.text
    assert resp.status_code == 200, resp.text[:500]
    ctype = resp.headers.get("content-type", "")
    assert "application/x-ndjson" in ctype, ctype
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


def _setup_two_agents(client, monkeypatch, route_fn=None):
    if route_fn is None:
        async def route_fn(payload):
            return ({"email": {"value": "a@b.in", "confidence": 0.9,
                               "confidence_type": "quoted",
                               "evidence": "mail me at a@b.in"}},
                    {"prompt_tokens": 10, "completion_tokens": 5,
                     "total_tokens": 15})
    monkeypatch.setattr(runs_router, "complete_json_payload", route_fn)
    aid1 = client.post("/api/v1/agents", json={"name": "S1"}).json()["id"]
    aid2 = client.post("/api/v1/agents", json={"name": "S2"}).json()["id"]
    client.post("/api/v1/attributes", json={"agent_ids": [aid1], "name": "email"})
    client.post("/api/v1/attributes", json={"agent_ids": [aid2], "name": "email"})
    return aid1, aid2


def test_runs_stream_events_and_single_log_row(client, monkeypatch):
    aid1, aid2 = _setup_two_agents(client, monkeypatch)
    before = len(client.get("/api/v1/logs").json())
    resp = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": [aid1, aid2], "model": "m", "stream": True})
    events = _parse_ndjson(resp)
    kinds = [e.get("type") for e in events]
    assert kinds[0] == "start"
    assert kinds[-1] == "done"
    assert "error" not in kinds
    start = events[0]
    assert start["model"] == "m"
    assert start["run_id"]
    assert start["log_id"]
    got_ids = {a["agent_id"] for a in start["agents"]}
    assert got_ids == {aid1, aid2}
    agent_evs = [e for e in events if e.get("type") == "agent"]
    assert len(agent_evs) == 2
    assert {e["agent_id"] for e in agent_evs} == {aid1, aid2}
    for ev in agent_evs:
        assert ev["status"] in ("done", "error")
        assert "output" in ev and "usage" in ev
        assert ev["output"]["email"]["value"] == "a@b.in"
    done = events[-1]
    log = done["log"]
    assert log["id"] == done["log_id"] == start["log_id"]
    assert log["run_id"] == start["run_id"]
    assert set(log["outputs"].keys()) == {aid1, aid2}
    # Same shape as the non-streaming response's log object.
    plain = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": [aid1, aid2], "model": "m"}).json()
    assert set(log.keys()) == set(plain["log"].keys())
    assert set(log["outputs"].keys()) == set(plain["outputs"].keys())
    assert set(log["usage"].keys()) == set(plain["usage"].keys())
    # Exactly one RunLog row per streamed run.
    after = client.get("/api/v1/logs").json()
    # Two runs above (1 stream + 1 plain) added two rows.
    assert len(after) == before + 2


def test_runs_stream_completion_order(client, monkeypatch):
    import app.modules.runs.router as rr
    aid1 = client.post(
        "/api/v1/agents",
        json={"name": "Slow", "system_instruction": "sys-slow"}).json()["id"]
    aid2 = client.post(
        "/api/v1/agents",
        json={"name": "Fast", "system_instruction": "sys-fast"}).json()["id"]
    client.post("/api/v1/attributes", json={"agent_ids": [aid1], "name": "email"})
    client.post("/api/v1/attributes", json={"agent_ids": [aid2], "name": "email"})

    async def _sleepy(payload):
        text = payload["messages"][0]["content"]
        key = "slow" if "sys-slow" in text else "fast"
        await asyncio.sleep(0.3 if key == "slow" else 0.05)
        return ({"email": {"value": key, "confidence": 0.9,
                           "confidence_type": "quoted", "evidence": "e"}},
                {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    async def _no_price(model):
        return (None, None)

    monkeypatch.setattr(rr, "complete_json_payload", _sleepy)
    monkeypatch.setattr(rr, "get_model_pricing", _no_price)
    resp = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hi",
        "agent_ids": [aid1, aid2], "model": "m", "stream": True})
    events = _parse_ndjson(resp)
    agent_evs = [e for e in events if e.get("type") == "agent"]
    assert len(agent_evs) == 2
    # Completion order: fast first even though slow was requested first.
    assert agent_evs[0]["agent_id"] == aid2
    assert agent_evs[1]["agent_id"] == aid1
    # Final log still in agent order like the non-streaming path.
    done = next(e for e in events if e.get("type") == "done")
    assert list(done["log"]["outputs"].keys()) == [aid1, aid2]


def test_runs_stream_reused_sent_immediately(client, monkeypatch):
    aid = client.post("/api/v1/agents", json={"name": "R"}).json()["id"]
    client.post("/api/v1/attributes", json={"agent_ids": [aid], "name": "email"})

    async def _first(payload):
        return ({"email": {"value": "x", "confidence": 0.9,
                           "confidence_type": "quoted", "evidence": "e"}},
                {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5})

    monkeypatch.setattr(runs_router, "complete_json_payload", _first)
    first = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [aid], "model": "m"}).json()
    src_log = first["log_id"]

    async def _boom(payload):
        raise AssertionError("reused agent must not call OpenRouter")

    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)
    resp = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": [aid], "model": "m",
        "reuse": {aid: src_log}, "stream": True})
    events = _parse_ndjson(resp)
    agent_evs = [e for e in events if e.get("type") == "agent"]
    assert len(agent_evs) == 1
    assert agent_evs[0].get("reused") is True
    assert agent_evs[0]["output"] == first["outputs"][aid]


def _setup_auto(client):
    def _mk(name, attrs):
        aid = client.post(
            "/api/v1/agents",
            json={"name": name, "system_instruction": f"sys-{name}"}).json()["id"]
        for a in attrs:
            client.post("/api/v1/attributes", json={"agent_ids": [aid], "name": a})
        return aid
    beh = _mk("behavioral", ["b_attr"])
    qry = _mk("query", ["q_attr"])
    fb = _mk("feedback", ["fb_attr"])
    ident = client.post(
        "/api/v1/agents",
        json={"name": "agent_identifier", "kind": "identifier"}).json()["id"]
    return ident, beh, qry, fb


def _filled(v):
    return {"value": v, "confidence": 0.9, "confidence_type": "quoted",
            "evidence": "e"}


def test_auto_stream_events_and_single_log_row(client, monkeypatch):
    from app.modules.runs.router import IDENTIFIER_QUESTIONS
    ident, beh, qry, fb = _setup_auto(client)

    async def _route(payload):
        rf = payload.get("response_format", {})
        js = rf.get("json_schema", {}) if isinstance(rf, dict) else {}
        if isinstance(js, dict) and js.get("name") == "agent_selection":
            return ({q["key"]: False for q in IDENTIFIER_QUESTIONS},
                    {"prompt_tokens": 5, "completion_tokens": 5,
                     "total_tokens": 10})
        schema = {}
        try:
            schema = js.get("schema", {})
        except Exception:
            schema = {}
        required = schema.get("required", []) if isinstance(schema, dict) else []
        if "b_attr" in required:
            return ({"b_attr": _filled("x")},
                    {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        if "q_attr" in required:
            return ({"q_attr": _filled("y")},
                    {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        return ({"fb_attr": _filled("z")},
                {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    async def _known(model):
        return (0.000001, 0.000002)

    monkeypatch.setattr(runs_router, "complete_json_payload", _route)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known)
    before = len(client.get("/api/v1/logs").json())
    resp = client.post("/api/v1/runs/auto", json={
        "input_type": "mail", "input_data": "hello", "model": "m",
        "meeting_type": "Other", "stream": True})
    events = _parse_ndjson(resp)
    kinds = [e.get("type") for e in events]
    assert kinds[0] == "start"
    assert kinds[-1] == "done"
    assert "identifier" in kinds
    start = events[0]
    assert start["model"] == "m"
    assert start["run_id"] and start["log_id"]
    agent_ids = {a["agent_id"] for a in start["agents"]}
    assert ident in agent_ids
    agent_evs = [e for e in events if e.get("type") == "agent"]
    # One agent event per planned agent (identifier covered by identifier event
    # plus its own agent event when present); at minimum the always-run agents.
    assert {beh, qry, fb} <= {e["agent_id"] for e in agent_evs}
    ident_ev = next(e for e in events if e.get("type") == "identifier")
    assert ident_ev["agent_id"] == ident
    assert isinstance(ident_ev.get("answers"), dict)
    assert isinstance(ident_ev.get("plan"), list)
    done = events[-1]
    log = done["log"]
    assert log["id"] == done["log_id"] == start["log_id"]
    # Same shape as non-streaming auto log.
    plain = client.post("/api/v1/runs/auto", json={
        "input_type": "mail", "input_data": "hello", "model": "m",
        "meeting_type": "Other"}).json()
    assert set(log.keys()) == set(plain["log"].keys())
    assert set(log["outputs"].keys()) == set(plain["outputs"].keys())
    after = client.get("/api/v1/logs").json()
    assert len(after) == before + 2
