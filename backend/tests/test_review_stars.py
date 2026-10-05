"""Coverage: server-persisted review stars JSON file (GET/PUT /api/v1/review/stars)."""


def _env(monkeypatch, tmp_path, name="review_stars.json"):
    p = tmp_path / name
    monkeypatch.setenv("REVIEW_STARS_PATH", str(p))
    return p


def test_missing_file_returns_empty(client, monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    r = client.get("/api/v1/review/stars")
    assert r.status_code == 200
    assert r.json() == {"stars": []}


def test_roundtrip_put_get_sorted(client, monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    r = client.put("/api/v1/review/stars", json={"stars": ["b|q2", "a|q1"]})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "count": 2}
    r = client.get("/api/v1/review/stars")
    assert r.json() == {"stars": ["a|q1", "b|q2"]}


def test_dedupe_sort_ignore_non_strings(client, monkeypatch, tmp_path):
    p = _env(monkeypatch, tmp_path)
    payload = {"stars": ["b", "a", "b", 1, None, {"x": 1}, ["y"], "a"]}
    r = client.put("/api/v1/review/stars", json=payload)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "count": 2}
    assert r.json()["count"] == 2
    assert client.get("/api/v1/review/stars").json() == {"stars": ["a", "b"]}
    import json

    assert json.loads(p.read_text(encoding="utf-8")) == {"stars": ["a", "b"]}


def test_corrupt_file_returns_empty(client, monkeypatch, tmp_path):
    p = _env(monkeypatch, tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not valid json", encoding="utf-8")
    r = client.get("/api/v1/review/stars")
    assert r.status_code == 200
    assert r.json() == {"stars": []}


def test_bad_body_returns_422(client, monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    assert client.put("/api/v1/review/stars", json={"stars": "not-a-list"}).status_code == 422
    assert client.put("/api/v1/review/stars", json={}).status_code == 422
