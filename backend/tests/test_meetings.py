"""Meetings endpoints: 503-shape when settings empty + transcript joining."""

import app.modules.meetings.router as meetings_router
from app.core.config import settings


def _empty_settings(monkeypatch):
    monkeypatch.setattr(settings, "mongodb_uri", "")
    monkeypatch.setattr(settings, "fireflies_api_key", "")
    meetings_router.clear_cache()


def test_clients_503_when_no_mongo(client, monkeypatch):
    _empty_settings(monkeypatch)
    resp = client.get("/api/v1/meetings/clients")
    assert resp.status_code == 503
    assert "MONGODB_URI" in resp.json()["detail"]
    assert "backend/.env" in resp.json()["detail"]


def test_titles_503_when_no_fireflies_key(client, monkeypatch):
    _empty_settings(monkeypatch)
    resp = client.get("/api/v1/meetings/titles", params={"client_id": "C001"})
    assert resp.status_code == 503
    assert "FIREFLIES_API_KEY" in resp.json()["detail"]


def test_meetings_503_when_no_fireflies_key(client, monkeypatch):
    _empty_settings(monkeypatch)
    resp = client.get("/api/v1/meetings", params={"client_id": "C001"})
    assert resp.status_code == 503
    assert "FIREFLIES_API_KEY" in resp.json()["detail"]


def test_transcript_503_when_no_fireflies_key(client, monkeypatch):
    _empty_settings(monkeypatch)
    resp = client.get("/api/v1/meetings/abc123/transcript")
    assert resp.status_code == 503
    assert "FIREFLIES_API_KEY" in resp.json()["detail"]


def test_build_transcription_joins_sentences():
    sentences = [
        {"speaker_name": "Anita", "text": "Hello there"},
        {"speaker_name": "", "text": "No name fallback"},
        {"text": "Missing key fallback"},
        {"speaker_name": "Bob", "text": "  "},
        {"speaker_name": "Bob", "text": "Second line"},
    ]
    text = meetings_router.build_transcription(sentences)
    assert text == (
        "Anita: Hello there\n"
        "Speaker: No name fallback\n"
        "Speaker: Missing key fallback\n"
        "Bob: Second line"
    )
    assert isinstance(text, str)


def test_titles_filters_by_client_match(client, monkeypatch):
    monkeypatch.setattr(settings, "fireflies_api_key", "fake-key")
    monkeypatch.setattr(settings, "mongodb_uri", "mongodb://fake")
    meetings_router.clear_cache()

    monkeypatch.setattr(
        meetings_router, "_fetch_mongo_client_doc",
        lambda cid: {"clientId": cid, "fullName": "Acme Corp", "email": ["owner@acme.com"]},
    )
    fake = [
        {"id": "m1", "title": "Acme Corp and Turtle | Tax Planning",
         "participants": ["someone@else.com"], "organizer_email": "",
         "meeting_attendees": []},
        {"id": "m2", "title": "Other and Turtle | Review",
         "participants": ["owner@acme.com"], "organizer_email": "",
         "meeting_attendees": []},
        {"id": "m3", "title": "Unrelated and Turtle | Review",
         "participants": ["nobody@x.com"], "organizer_email": "",
         "meeting_attendees": []},
    ]
    monkeypatch.setattr(meetings_router, "_fetch_transcripts_list", lambda **kw: fake)

    body = client.get("/api/v1/meetings/titles", params={"client_id": "C001"}).json()
    assert body == {"titles": ["Acme Corp and Turtle | Tax Planning", "Other and Turtle | Review"]} \
        or sorted(body["titles"]) == sorted(["Acme Corp and Turtle | Tax Planning", "Other and Turtle | Review"])


def test_meetings_exact_title_and_transcript_shape(client, monkeypatch):
    monkeypatch.setattr(settings, "fireflies_api_key", "fake-key")
    monkeypatch.setattr(settings, "mongodb_uri", "")
    meetings_router.clear_cache()

    fake = [
        {"id": "m1", "title": "Acme and Turtle | Kick-off", "date": "2026-09-30T10:00:00.000Z",
         "duration": 30, "participants": ["a@turtlefinance.in"], "organizer_email": "",
         "meeting_attendees": []},
        {"id": "m2", "title": "Acme and Turtle | Review", "date": "2026-09-30T11:00:00.000Z",
         "duration": None, "participants": [], "organizer_email": "",
         "meeting_attendees": [{"email": "b@turtlefinance.in", "displayName": "B"}]},
    ]
    monkeypatch.setattr(meetings_router, "_fetch_transcripts_list", lambda **kw: fake)

    body = client.get("/api/v1/meetings", params={"title": "Acme and Turtle | Review"}).json()
    assert len(body["meetings"]) == 1
    m = body["meetings"][0]
    assert m["id"] == "m2"
    assert m["duration_min"] is None
    assert m["participants"] == ["b@turtlefinance.in"]
    assert isinstance(m["date"], str) and m["date"]

    monkeypatch.setattr(
        meetings_router, "_fetch_transcript_detail",
        lambda mid: {"id": mid, "title": "T", "date": "2026-09-30T10:00:00.000Z",
                     "sentences": [{"speaker_name": "A", "text": "hi"},
                                   {"speaker_name": "", "text": "there"}]},
    )
    t = client.get("/api/v1/meetings/m1/transcript").json()
    assert t["id"] == "m1"
    assert t["transcription"] == "A: hi\nSpeaker: there"
    assert "sentences" not in t

    monkeypatch.setattr(meetings_router, "_fetch_transcript_detail", lambda mid: None)
    assert client.get("/api/v1/meetings/missing/transcript").status_code == 404
