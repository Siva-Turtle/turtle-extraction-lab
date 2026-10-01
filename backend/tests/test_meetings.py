"""Meetings endpoints against Mongo `tasks` (mocked pymongo; no live Mongo)."""

from datetime import datetime, timezone

from bson import ObjectId

import app.modules.meetings.router as meetings_router
from app.core.config import settings


def _matches(doc, filt):
    for key, cond in filt.items():
        if isinstance(cond, dict) and "$in" in cond:
            if not any(doc.get(key) == allow for allow in cond["$in"]):
                return False
        elif doc.get(key) != cond:
            return False
    return True


def _apply_proj(doc, proj):
    if not proj:
        return dict(doc)
    out = {}
    for key, include in proj.items():
        if include and key in doc:
            out[key] = doc[key]
    if "_id" in doc and proj.get("_id", 1):
        out["_id"] = doc["_id"]
    return out


class FakeCollection:
    def __init__(self, docs):
        self._docs = list(docs)

    def find(self, filt=None, proj=None):
        return [_apply_proj(d, proj) for d in self._docs if _matches(d, filt or {})]

    def find_one(self, filt=None, proj=None):
        for doc in self.find(filt, proj):
            return doc
        return None


class FakeDB:
    def __init__(self, collections):
        self._collections = collections

    def __getitem__(self, name):
        return self._collections[name]


class FakeClient:
    def __init__(self, clients, tasks):
        self._db = FakeDB({
            "clients": FakeCollection(clients),
            "tasks": FakeCollection(tasks),
        })

    def __getitem__(self, name):
        assert name == "turtle-finance-db"
        return self._db

    def close(self):
        pass


def _install(monkeypatch, clients, tasks):
    monkeypatch.setattr(settings, "mongodb_uri", "mongodb://fake")
    monkeypatch.setattr(
        meetings_router, "_mongo_client", lambda uri: FakeClient(clients, tasks)
    )
    meetings_router.clear_cache()


def _empty_settings(monkeypatch):
    monkeypatch.setattr(settings, "mongodb_uri", "")
    meetings_router.clear_cache()


C1_ID = ObjectId()
C2_ID = ObjectId()
T1_ID = ObjectId()

CLIENTS = [
    {"_id": C1_ID, "clientId": "C001", "fullName": "Acme Corp",
     "email": ["owner@acme.com"]},
    {"_id": C2_ID, "clientId": "C002", "fullName": "Beta LLC",
     "email": ["owner@beta.com"]},
]

TASKS = [
    {
        "_id": T1_ID,
        "client": C1_ID,  # ObjectId form
        "title": "Tax Planning Conversation",
        "date": datetime(2026, 9, 30, 4, 0, tzinfo=timezone.utc),
        "createdAt": datetime(2026, 9, 30, 4, 0, tzinfo=timezone.utc),
        "duration": 45,
        "participants": ["a@x.com", {"email": "b@y.com"}],
        "fullTranscript": "Anita: Hello\nBob:  extra spaces kept  ",
        "detailedNotes": "notes",
        "summary": "summary",
    },
    {
        "_id": ObjectId(),
        "client": str(C1_ID),  # str form of the same client ref
        "title": "Kick-off Conversation",
        "date": "2026-09-29T10:00:00.000Z",
        "createdAt": "2026-09-29T10:00:00.000Z",
        "participants": "not-a-list",
        "fullTranscript": "",
        "detailedNotes": "kickoff notes",
        "summary": "kickoff summary",
    },
    {
        "_id": ObjectId(),
        "client": C2_ID,
        "title": "Other Client Meeting",
        "date": datetime(2026, 9, 30, 6, 0, tzinfo=timezone.utc),
        "createdAt": datetime(2026, 9, 30, 6, 0, tzinfo=timezone.utc),
        "participants": [],
        "fullTranscript": "other",
        "detailedNotes": "",
        "summary": "",
    },
]


def test_clients_503_when_no_mongo(client, monkeypatch):
    _empty_settings(monkeypatch)
    resp = client.get("/api/v1/meetings/clients")
    assert resp.status_code == 503
    assert "MONGODB_URI" in resp.json()["detail"]
    assert "backend/.env" in resp.json()["detail"]


def test_titles_503_when_no_mongo(client, monkeypatch):
    _empty_settings(monkeypatch)
    resp = client.get("/api/v1/meetings/titles", params={"client_id": "C001"})
    assert resp.status_code == 503
    assert "MONGODB_URI" in resp.json()["detail"]


def test_meetings_503_when_no_mongo(client, monkeypatch):
    _empty_settings(monkeypatch)
    resp = client.get("/api/v1/meetings", params={"client_id": "C001"})
    assert resp.status_code == 503
    assert "MONGODB_URI" in resp.json()["detail"]


def test_transcript_503_when_no_mongo(client, monkeypatch):
    _empty_settings(monkeypatch)
    resp = client.get("/api/v1/meetings/abc123/transcript")
    assert resp.status_code == 503
    assert "MONGODB_URI" in resp.json()["detail"]


def test_titles_for_client_matches_both_id_forms(client, monkeypatch):
    _install(monkeypatch, CLIENTS, TASKS)
    body = client.get("/api/v1/meetings/titles", params={"client_id": "C001"}).json()
    assert body == {
        "titles": ["Kick-off Conversation", "Tax Planning Conversation"]
    }


def test_titles_unknown_client_empty(client, monkeypatch):
    _install(monkeypatch, CLIENTS, TASKS)
    assert client.get("/api/v1/meetings/titles", params={"client_id": "NOPE"}).json() == {
        "titles": []
    }


def test_titles_no_client_caps_at_500_most_recent(client, monkeypatch):
    from datetime import timedelta

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    tasks = [
        {
            "_id": ObjectId(),
            "title": f"Meeting {i:04d}",
            "date": base + timedelta(days=i),
            "createdAt": base + timedelta(days=i),
        }
        for i in range(505)
    ]
    _install(monkeypatch, CLIENTS, tasks)
    body = client.get("/api/v1/meetings/titles").json()
    assert len(body["titles"]) == 500
    assert "Meeting 0000" not in body["titles"]  # oldest drops out
    assert "Meeting 0504" in body["titles"]
    assert body["titles"] == sorted(body["titles"], key=str.lower)


def test_meetings_exact_title_shape(client, monkeypatch):
    _install(monkeypatch, CLIENTS, TASKS)
    body = client.get(
        "/api/v1/meetings",
        params={"client_id": "C001", "title": "Tax Planning Conversation"},
    ).json()
    assert len(body["meetings"]) == 1
    m = body["meetings"][0]
    assert m["id"] == str(T1_ID)
    assert m["title"] == "Tax Planning Conversation"
    assert m["duration_min"] == 45.0
    assert m["participants"] == ["a@x.com", "b@y.com"]
    assert isinstance(m["date"], str) and m["date"].startswith("2026-09-30")


def test_meetings_date_ist_day_and_tolerant_parsing(client, monkeypatch):
    epoch_ms = int(datetime(2026, 9, 30, 5, 0, tzinfo=timezone.utc).timestamp() * 1000)
    tasks = list(TASKS) + [
        {
            "_id": ObjectId(),
            "client": C1_ID,
            "title": "Epoch Meeting",
            "date": epoch_ms,
            "createdAt": epoch_ms,
            "participants": [],
            "fullTranscript": "e",
            "detailedNotes": "",
            "summary": "",
        }
    ]
    _install(monkeypatch, CLIENTS, tasks)
    body = client.get(
        "/api/v1/meetings", params={"client_id": "C001", "date": "2026-09-30"}
    ).json()
    titles = sorted(m["title"] for m in body["meetings"])
    assert titles == ["Epoch Meeting", "Tax Planning Conversation"]
    # Most recent first.
    assert body["meetings"][0]["title"] == "Epoch Meeting"


def test_meetings_missing_duration_and_nonlist_participants(client, monkeypatch):
    _install(monkeypatch, CLIENTS, TASKS)
    body = client.get(
        "/api/v1/meetings",
        params={"client_id": "C001", "title": "Kick-off Conversation"},
    ).json()
    assert len(body["meetings"]) == 1
    assert body["meetings"][0]["duration_min"] is None
    assert body["meetings"][0]["participants"] == []


def test_meetings_unknown_client_empty(client, monkeypatch):
    _install(monkeypatch, CLIENTS, TASKS)
    assert client.get("/api/v1/meetings", params={"client_id": "NOPE"}).json() == {
        "meetings": []
    }


def test_meetings_cap_500(client, monkeypatch):
    tasks = [
        {
            "_id": ObjectId(),
            "client": C1_ID,
            "title": "Bulk",
            "date": datetime(2026, 9, 30, 4, 0, tzinfo=timezone.utc),
            "createdAt": datetime(2026, 9, 30, 4, 0, tzinfo=timezone.utc),
            "participants": [],
        }
        for _ in range(505)
    ]
    _install(monkeypatch, CLIENTS, tasks)
    body = client.get("/api/v1/meetings", params={"client_id": "C001"}).json()
    assert len(body["meetings"]) == 500


def test_meetings_422_bad_date(client, monkeypatch):
    _install(monkeypatch, CLIENTS, TASKS)
    resp = client.get("/api/v1/meetings", params={"date": "30-09-2026"})
    assert resp.status_code == 422


def test_transcript_returns_full_verbatim(client, monkeypatch):
    _install(monkeypatch, CLIENTS, TASKS)
    body = client.get(f"/api/v1/meetings/{T1_ID}/transcript").json()
    assert body["id"] == str(T1_ID)
    assert body["title"] == "Tax Planning Conversation"
    assert body["transcription"] == "Anita: Hello\nBob:  extra spaces kept  "
    assert "sentences" not in body


def test_transcript_fallback_chain(client, monkeypatch):
    det_id, sum_id = ObjectId(), ObjectId()
    tasks = list(TASKS) + [
        {"_id": det_id, "title": "D", "date": "2026-09-30T10:00:00.000Z",
         "fullTranscript": "  ", "detailedNotes": "detail text", "summary": "s"},
        {"_id": sum_id, "title": "S", "date": "2026-09-30T10:00:00.000Z",
         "fullTranscript": "", "detailedNotes": "", "summary": "summary text"},
    ]
    _install(monkeypatch, CLIENTS, tasks)
    assert client.get(f"/api/v1/meetings/{det_id}/transcript").json()["transcription"] == (
        "detail text"
    )
    assert client.get(f"/api/v1/meetings/{sum_id}/transcript").json()["transcription"] == (
        "summary text"
    )


def test_transcript_404_when_all_empty(client, monkeypatch):
    empty_id = ObjectId()
    tasks = list(TASKS) + [
        {"_id": empty_id, "title": "E", "date": "2026-09-30T10:00:00.000Z",
         "fullTranscript": "", "detailedNotes": " ", "summary": ""},
    ]
    _install(monkeypatch, CLIENTS, tasks)
    assert client.get(f"/api/v1/meetings/{empty_id}/transcript").status_code == 404
    assert client.get(f"/api/v1/meetings/{ObjectId()}/transcript").status_code == 404


def test_transcript_str_id_fallback(client, monkeypatch):
    tasks = list(TASKS) + [
        {"_id": "task-str-1", "title": "Str", "date": "2026-09-30T10:00:00.000Z",
         "fullTranscript": "plain", "detailedNotes": "", "summary": ""},
    ]
    _install(monkeypatch, CLIENTS, tasks)
    body = client.get("/api/v1/meetings/task-str-1/transcript").json()
    assert body["id"] == "task-str-1"
    assert body["transcription"] == "plain"
