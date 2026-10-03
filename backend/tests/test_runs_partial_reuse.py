"""Per-agent already-exists matching + partial reuse (backend only)."""

import copy

import app.modules.runs.router as runs_router


def _make_agents(client, names):
    aids = []
    for n in names:
        aid = client.post(
            "/api/v1/agents",
            json={"name": n, "system_instruction": f"sys-{n}"}).json()["id"]
        client.post("/api/v1/attributes", json={"agent_ids": [aid], "name": "email"})
        aids.append(aid)
    return aids


def _check(client, agent_ids, models, input_data="mail me at a@b.in"):
    body = {"input_type": "mail", "input_data": input_data,
            "agent_ids": agent_ids,
            "models": models}
    resp = client.post("/api/v1/runs/check-existing", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _ok_complete(payload):
    return ({"ok": 1}, {"prompt_tokens": 10, "completion_tokens": 5,
                         "total_tokens": 15})


async def _known_pricing(model):
    return (0.000001, 0.000002)


# --- check-existing -------------------------------------------------------

def test_check_partial_single_agent_log_matches_only_it(client, monkeypatch):
    """(a) earlier 1-agent log matches only that agent when 4 requested."""
    aids = _make_agents(client, ["A1", "A2", "A3", "A4"])
    monkeypatch.setattr(runs_router, "complete_json_payload", _ok_complete)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)
    first = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": [aids[0]], "model": "m"}).json()

    async def _boom(payload):
        raise AssertionError("check-existing must not call OpenRouter")

    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)
    data = _check(client, aids, [{"model": "m", "reasoning_effort": ""}])
    assert len(data["slots"]) == 1
    slot = data["slots"][0]
    assert slot["model"] == "m"
    assert slot["reasoning_effort"] == ""
    assert len(slot["agents"]) == 1
    entry = slot["agents"][0]
    assert entry["agent_id"] == aids[0]
    assert entry["log_id"] == first["log_id"]
    assert entry["created_at"]
    # cost/duration come from the source per-agent entry.
    src_per = first["usage"]["per_agent"][aids[0]]
    assert entry["cost_usd"] == src_per["cost_usd"]
    assert entry["duration_ms"] == src_per["duration_ms"]


def test_check_partial_errored_agent_not_matched(client, monkeypatch):
    """(b) 4-agent log where one errored -> that agent unmatched, other 3 match."""
    aids = _make_agents(client, ["B1", "B2", "B3", "B4"])

    async def _one_error(payload):
        text = payload["messages"][0]["content"]
        if "sys-B2" in text:
            raise RuntimeError("provider down")
        return ({"ok": 1}, {"prompt_tokens": 4, "completion_tokens": 6,
                             "total_tokens": 10})

    monkeypatch.setattr(runs_router, "complete_json_payload", _one_error)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)
    body = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "hello",
        "agent_ids": aids, "model": "m"}).json()
    assert "_error" in body["outputs"][aids[1]]
    for i in (0, 2, 3):
        assert "_error" not in body["outputs"][aids[i]]

    async def _boom(payload):
        raise AssertionError("check-existing must not call OpenRouter")

    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)
    data = _check(client, aids, [{"model": "m", "reasoning_effort": ""}],
                  input_data="hello")
    matched_ids = {e["agent_id"] for e in data["slots"][0]["agents"]}
    assert matched_ids == {aids[0], aids[2], aids[3]}


def test_check_partial_effort_or_prompt_no_match(client, monkeypatch):
    """(c) different effort or changed prompt -> no match."""
    aids = _make_agents(client, ["C1", "C2"])
    monkeypatch.setattr(runs_router, "complete_json_payload", _ok_complete)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)
    client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": aids, "model": "m"}).json()

    async def _boom(payload):
        raise AssertionError("check-existing must not call OpenRouter")

    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)
    # Different effort -> no match for either agent.
    assert _check(client, aids, [{"model": "m", "reasoning_effort": "high"}])[
        "slots"][0]["agents"] == []
    # Changed prompt (system instruction) -> no match.
    client.patch(f"/api/v1/agents/{aids[0]}", json={"system_instruction": "changed"})
    data = _check(client, aids, [{"model": "m", "reasoning_effort": ""}])
    matched = {e["agent_id"] for e in data["slots"][0]["agents"]}
    # C1 changed -> unmatched; C2 unchanged -> still matched.
    assert matched == {aids[1]}
    # Changed input text -> no match at all.
    assert _check(client, aids, [{"model": "m", "reasoning_effort": ""}],
                  input_data="something else")["slots"][0]["agents"] == []


def test_check_partial_per_slot_ordering(client, monkeypatch):
    """(d) one entry per requested slot, same order; matching is per-slot."""
    aids = _make_agents(client, ["D1"])
    monkeypatch.setattr(runs_router, "complete_json_payload", _ok_complete)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)
    client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": aids, "model": "m-a"}).json()

    async def _boom(payload):
        raise AssertionError("check-existing must not call OpenRouter")

    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)
    data = _check(client, aids, [{"model": "m-b"}, {"model": "m-a"}])
    assert [s["model"] for s in data["slots"]] == ["m-b", "m-a"]
    assert data["slots"][0]["agents"] == []
    assert len(data["slots"][1]["agents"]) == 1
    # Effort slots keep order too.
    data2 = _check(client, aids, [
        {"model": "m-a", "reasoning_effort": "high"},
        {"model": "m-a", "reasoning_effort": ""},
    ])
    assert [s["reasoning_effort"] for s in data2["slots"]] == ["high", ""]
    assert data2["slots"][0]["agents"] == []
    assert len(data2["slots"][1]["agents"]) == 1


def test_check_partial_chained_created_at_uses_original(client, monkeypatch):
    aids = _make_agents(client, ["E1"])
    monkeypatch.setattr(runs_router, "complete_json_payload", _ok_complete)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)
    first = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": aids, "model": "m"}).json()
    # All-reused copy of the first log.
    second = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": aids, "model": "m",
        "reuse": {aids[0]: first["log_id"]}}).json()
    assert second["log"]["reused_from_log_id"] == first["log_id"]

    async def _boom(payload):
        raise AssertionError("check-existing must not call OpenRouter")

    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)
    data = _check(client, aids, [{"model": "m"}])
    assert len(data["slots"][0]["agents"]) == 1
    entry = data["slots"][0]["agents"][0]
    # Newest matching log is the second (reused) one, but created_at is the
    # ORIGINAL generation time preserved through the chain.
    assert entry["log_id"] == second["log_id"]
    orig_per_created = second["usage"]["per_agent"][aids[0]]["reused_from_created_at"]
    assert entry["created_at"] == orig_per_created


# --- create_run reuse -----------------------------------------------------

def test_create_partial_reuse_copies_and_skips_call(client, monkeypatch):
    aids = _make_agents(client, ["F1", "F2"])
    monkeypatch.setattr(runs_router, "complete_json_payload", _ok_complete)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)
    first = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": aids, "model": "m"}).json()
    src_out = copy.deepcopy(first["outputs"][aids[0]])
    src_per = copy.deepcopy(first["usage"]["per_agent"][aids[0]])

    seen = {"calls": []}

    async def _capture(payload):
        seen["calls"].append(payload)
        return ({"fresh": True}, {"prompt_tokens": 7, "completion_tokens": 8,
                                  "total_tokens": 15})

    async def _pricing(model):
        return (0.000001, 0.000002)

    monkeypatch.setattr(runs_router, "complete_json_payload", _capture)
    monkeypatch.setattr(runs_router, "get_model_pricing", _pricing)
    second = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": aids, "model": "m",
        "reuse": {aids[0]: first["log_id"]}}).json()

    # Only the fresh agent was sent.
    assert len(seen["calls"]) == 1
    assert "sys-F2" in seen["calls"][0]["messages"][0]["content"]
    assert "sys-F1" not in seen["calls"][0]["messages"][0]["content"]
    # Output copied verbatim; requests stay freshly built (messages equal).
    assert second["outputs"][aids[0]] == src_out
    assert second["outputs"][aids[1]] == {"fresh": True}
    assert second["requests"][aids[0]]["messages"] == first["requests"][aids[0]]["messages"]
    # Costs copied, not repriced.
    per0 = second["usage"]["per_agent"][aids[0]]
    for k in ("prompt_tokens", "completion_tokens", "total_tokens",
              "reasoning_tokens", "cost_usd", "input_cost_usd",
              "output_cost_usd", "duration_ms", "model"):
        assert per0[k] == src_per[k], k
    assert per0["reused_from_log_id"] == first["log_id"]
    assert per0["reused_from_created_at"]
    # Totals include reused numbers.
    per1 = second["usage"]["per_agent"][aids[1]]
    assert second["usage"]["prompt_tokens"] == src_per["prompt_tokens"] + per1["prompt_tokens"]
    assert second["usage"]["completion_tokens"] == (
        src_per["completion_tokens"] + per1["completion_tokens"])
    assert second["usage"]["total_tokens"] == (
        second["usage"]["prompt_tokens"] + second["usage"]["completion_tokens"])
    # reused_agents populated only for the reused agent.
    assert set(second["usage"]["reused_agents"]) == {aids[0]}
    assert second["usage"]["reused_agents"][aids[0]]["log_id"] == first["log_id"]
    # Partial reuse -> log-level markers stay blank.
    assert second["log"]["reused_from_log_id"] == ""
    assert second["log"]["reused_from_created_at"] is None
    assert second["log"]["feedback"] == {}
    # Pricing loop did not overwrite reused costs.
    assert per0["cost_usd"] == src_per["cost_usd"]


def test_create_partial_invalid_reuse_falls_back(client, monkeypatch):
    aids = _make_agents(client, ["G1", "G2"])
    monkeypatch.setattr(runs_router, "complete_json_payload", _ok_complete)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)
    first = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": aids, "model": "m"}).json()

    seen = {"n": 0}

    async def _count(payload):
        seen["n"] += 1
        return ({"ok": 1}, {"prompt_tokens": 1, "completion_tokens": 1,
                             "total_tokens": 2})

    monkeypatch.setattr(runs_router, "complete_json_payload", _count)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)
    # Unknown log id -> ignored silently, both agents run.
    body = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": aids, "model": "m",
        "reuse": {aids[0]: "missing", aids[1]: first["log_id"]}}).json()
    assert seen["n"] == 1
    assert set(body["usage"]["reused_agents"]) == {aids[1]}

    # Wrong model -> invalid, runs normally.
    seen["n"] = 0
    body2 = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": aids, "model": "other",
        "reuse": {aids[0]: first["log_id"]}}).json()
    assert seen["n"] == 2
    assert body2["usage"]["reused_agents"] == {}

    # Changed prompt -> messages differ -> invalid, runs normally.
    client.patch(f"/api/v1/agents/{aids[0]}", json={"system_instruction": "changed"})
    seen["n"] = 0
    body3 = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": aids, "model": "m",
        "reuse": {aids[0]: first["log_id"], aids[1]: first["log_id"]}}).json()
    # aids[0] prompt changed so only aids[1] can reuse.
    assert seen["n"] == 1
    assert set(body3["usage"]["reused_agents"]) == {aids[1]}


def test_create_all_reused_zero_calls_and_log_markers(client, monkeypatch):
    aids = _make_agents(client, ["H1", "H2"])
    monkeypatch.setattr(runs_router, "complete_json_payload", _ok_complete)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)
    first = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": aids, "model": "m"}).json()

    async def _boom(payload):
        raise AssertionError("all-reused must not call OpenRouter")

    async def _boom_price(model):
        raise AssertionError("all-reused must not fetch pricing")

    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)
    monkeypatch.setattr(runs_router, "get_model_pricing", _boom_price)
    second = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": aids, "model": "m",
        "reuse": {a: first["log_id"] for a in aids}}).json()
    assert second["outputs"] == first["outputs"]
    assert set(second["usage"]["reused_agents"]) == set(aids)
    for a in aids:
        assert second["usage"]["per_agent"][a]["reused_from_log_id"] == first["log_id"]
        assert second["usage"]["reused_agents"][a]["log_id"] == first["log_id"]
    # Totals include reused numbers even with no fresh calls.
    assert second["usage"]["prompt_tokens"] == first["usage"]["prompt_tokens"]
    assert second["usage"]["cost_usd"] == first["usage"]["cost_usd"]
    # Log-level reused_from set to the single source.
    assert second["log"]["reused_from_log_id"] == first["log_id"]
    assert second["log"]["reused_from_created_at"] is not None
    # usage.duration is max(wall, reused durations).
    d0 = first["usage"]["per_agent"][aids[0]]["duration_ms"]
    d1 = first["usage"]["per_agent"][aids[1]]["duration_ms"]
    assert second["usage"]["duration_ms"] >= max(d0, d1) - 0.001


def test_create_all_reused_different_sources_no_log_marker(client, monkeypatch):
    aids = _make_agents(client, ["I1", "I2"])
    monkeypatch.setattr(runs_router, "complete_json_payload", _ok_complete)
    monkeypatch.setattr(runs_router, "get_model_pricing", _known_pricing)
    first = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": [aids[0]], "model": "m"}).json()
    second_log = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": [aids[1]], "model": "m"}).json()

    async def _boom(payload):
        raise AssertionError("must not call OpenRouter")

    monkeypatch.setattr(runs_router, "complete_json_payload", _boom)

    async def _no_price(model):
        return (None, None)

    monkeypatch.setattr(runs_router, "get_model_pricing", _no_price)
    third = client.post("/api/v1/runs", json={
        "input_type": "mail", "input_data": "mail me at a@b.in",
        "agent_ids": aids, "model": "m",
        "reuse": {aids[0]: first["log_id"], aids[1]: second_log["log_id"]}}).json()
    assert set(third["usage"]["reused_agents"]) == set(aids)
    # Different sources -> log-level stays blank.
    assert third["log"]["reused_from_log_id"] == ""
    assert third["log"]["reused_from_created_at"] is None
