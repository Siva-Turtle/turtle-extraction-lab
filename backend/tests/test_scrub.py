"""PII scrub port tests (synthetic fake data only — never live Mongo/PII).

Mirrors CRM-v1-old `backend/utils/piiMasking.test.js` cases, plus the
known-contact pass, collect_known_pii, and the scrubbed-only transcript /
runs-with-meeting_id endpoints (mocked Mongo + mocked OpenRouter).
"""

from bson import ObjectId

import app.modules.meetings.router as meetings_router
import app.modules.runs.router as runs_router
from app.core.config import settings
from app.core.scrub import (
    collect_known_pii,
    mask_for_classifier,
    mask_generic_pii,
    mask_known_contact_pii,
)

# ---------------------------------------------------------------------------
# Generic pass — mirrors piiMasking.test.js
# ---------------------------------------------------------------------------


def test_generic_pan():
    masked = mask_generic_pii("My PAN is ABCDE1234F, please update KYC.")
    assert "ABCDE1234F" not in masked
    assert "[PAN]" in masked


def test_generic_aadhaar_spaced():
    masked = mask_generic_pii("Aadhaar number 1234 5678 9012 for verification.")
    assert "1234 5678 9012" not in masked
    assert "[AADHAAR]" in masked


def test_generic_ifsc():
    masked = mask_generic_pii("Please transfer to IFSC HDFC0001234.")
    assert "HDFC0001234" not in masked
    assert "[IFSC]" in masked


def test_generic_phone_with_country_prefix():
    masked = mask_generic_pii("Call me on +91 9876543210 after 5pm.")
    assert "9876543210" not in masked
    assert "[PHONE]" in masked


def test_generic_email():
    masked = mask_generic_pii("Send the statement to fake.person@example.com please.")
    assert "fake.person@example.com" not in masked
    assert "[EMAIL]" in masked


def test_generic_card_not_double_matched():
    masked = mask_generic_pii("Card 4111 1111 1111 1111 was declined.")
    assert "4111 1111 1111 1111" not in masked
    assert "[CARD]" in masked
    assert "[AADHAAR]" not in masked
    assert "[PHONE]" not in masked


def test_generic_ssn_hyphenated():
    masked = mask_generic_pii("SSN - 724-79-7967")
    assert "724-79-7967" not in masked
    assert "[SSN]" in masked


def test_generic_ssn_spaced_not_aadhaar():
    masked = mask_generic_pii("My SSN is 724 79 7967 for the tax filing.")
    assert "724 79 7967" not in masked
    assert "[SSN]" in masked
    assert "[AADHAAR]" not in masked


def test_generic_mixed_message():
    msg = (
        "Hi, my PAN is ABCDE1234F and Aadhaar is 1234 5678 9012. "
        "Call +91 9876543210 or email fake.person@example.com."
    )
    masked = mask_generic_pii(msg)
    assert "ABCDE1234F" not in masked
    assert "1234 5678 9012" not in masked
    assert "9876543210" not in masked
    assert "fake.person@example.com" not in masked
    assert "[PAN]" in masked
    assert "[AADHAAR]" in masked
    assert "[PHONE]" in masked
    assert "[EMAIL]" in masked


def test_generic_no_pii_passthrough():
    msg = "Can you please share the tax filing deadline for this quarter?"
    assert mask_generic_pii(msg) == msg


def test_generic_empty_none_passthrough():
    assert mask_generic_pii("") == ""
    assert mask_generic_pii(None) is None


def test_classifier_applies_generic_with_none_doc():
    masked = mask_for_classifier("My PAN is ABCDE1234F and Aadhaar is 1234 5678 9012.", None)
    assert "ABCDE1234F" not in masked
    assert "1234 5678 9012" not in masked
    assert "[PAN]" in masked
    assert "[AADHAAR]" in masked


# ---------------------------------------------------------------------------
# Known-contact pass
# ---------------------------------------------------------------------------


def test_known_contact_name_phone_email():
    masked = mask_known_contact_pii(
        "Alex Synthetic called from +91 91234 56780, email alex.synthetic@example.com",
        names=["Alex Synthetic"],
        phones=["+91 9123456780"],
        emails=["alex.synthetic@example.com"],
    )
    assert "Alex Synthetic" not in masked
    assert "91234 56780" not in masked
    assert "alex.synthetic@example.com" not in masked
    assert "[CLIENT_NAME]" in masked
    assert "[PHONE]" in masked
    assert "[EMAIL]" in masked


def test_known_contact_name_case_insensitive():
    masked = mask_known_contact_pii(
        "Meeting with ALEX SYNTHETIC today.", names=["Alex Synthetic"]
    )
    assert "ALEX SYNTHETIC" not in masked
    assert "[CLIENT_NAME]" in masked


def test_known_contact_short_values_skipped():
    text = "Call 12345 or ask X about it."
    masked = mask_known_contact_pii(text, names=["X"], phones=["12345"])
    assert masked == text  # <7-digit phone and <2-char name never match


def test_known_contact_falsy_text_passthrough():
    assert mask_known_contact_pii("", names=["Alex"]) == ""
    assert mask_known_contact_pii(None, names=["Alex"]) is None


def test_collect_known_pii_shapes():
    doc = {
        "fullName": "Alex Synthetic",
        "spouseName": "Sam Synthetic",
        "phone": "+91 9123456780",
        "phone2": "",
        "spousePhone": None,
        "email": "alex.synthetic@example.com",
    }
    assert collect_known_pii(doc) == {
        "names": ["Alex Synthetic", "Sam Synthetic"],
        "phones": ["+91 9123456780"],
        "emails": ["alex.synthetic@example.com"],
    }
    # Email may be a list; falsy entries dropped.
    doc_list = dict(doc, email=["a@example.com", "", None])
    assert collect_known_pii(doc_list)["emails"] == ["a@example.com"]
    assert collect_known_pii(None) == {"names": [], "phones": [], "emails": []}


def test_classifier_known_then_generic():
    doc = {
        "fullName": "Alex Synthetic",
        "phone": "+91 9123456780",
        "email": "alex.synthetic@example.com",
    }
    masked = mask_for_classifier(
        "Alex Synthetic (PAN ABCDE1234F) wrote from alex.synthetic@example.com.",
        doc,
    )
    assert "Alex Synthetic" not in masked
    assert "ABCDE1234F" not in masked
    assert "alex.synthetic@example.com" not in masked
    assert "[CLIENT_NAME]" in masked
    assert "[PAN]" in masked
    assert "[EMAIL]" in masked


# ---------------------------------------------------------------------------
# Endpoint tests — mocked Mongo, synthetic fake data only
# ---------------------------------------------------------------------------


def _matches(doc, filt):
    for key, cond in filt.items():
        if isinstance(cond, dict) and "$in" in cond:
            if not any(doc.get(key) == allow for allow in cond["$in"]):
                return False
        elif doc.get(key) != cond:
            return False
    return True


class _FakeCollection:
    def __init__(self, docs):
        self._docs = list(docs)

    def find(self, filt=None, proj=None):
        out = []
        for d in self._docs:
            if not _matches(d, filt or {}):
                continue
            if not proj:
                out.append(dict(d))
                continue
            row = {k for k in d if proj.get(k)}
            keep = {k: d[k] for k in row}
            if "_id" in d and proj.get("_id", 1):
                keep["_id"] = d["_id"]
            out.append(keep)
        return out

    def find_one(self, filt=None, proj=None):
        for doc in self.find(filt, proj):
            return doc
        return None


class _FakeDB:
    def __init__(self, collections):
        self._collections = collections

    def __getitem__(self, name):
        return self._collections[name]


class _FakeMongoClient:
    def __init__(self, clients, tasks):
        self._db = _FakeDB(
            {"clients": _FakeCollection(clients), "tasks": _FakeCollection(tasks)}
        )

    def __getitem__(self, name):
        assert name == "turtle-finance-db"
        return self._db

    def close(self):
        pass


_FAKE_CLIENT_ID = ObjectId()
_FAKE_TASK_ID = ObjectId()
_FAKE_CLIENTS = [
    {
        "_id": _FAKE_CLIENT_ID,
        "clientId": "SYNTH001",
        "fullName": "Alex Synthetic",
        "phone": "+91 9123456780",
        "email": ["alex.synthetic@example.com"],
    }
]
_FAKE_TASKS = [
    {
        "_id": _FAKE_TASK_ID,
        "client": _FAKE_CLIENT_ID,
        "title": "Synthetic Review Conversation",
        "date": "2026-09-30T10:00:00.000Z",
        "fullTranscript": (
            "Alex Synthetic confirmed PAN ABCDE1234F on call +91 9123456780; "
            "receipt to alex.synthetic@example.com."
        ),
        "detailedNotes": "",
        "summary": "",
    }
]


def _install_mongo(monkeypatch):
    monkeypatch.setattr(settings, "mongodb_uri", "mongodb://fake")
    monkeypatch.setattr(
        meetings_router, "_mongo_client", lambda uri: _FakeMongoClient(_FAKE_CLIENTS, _FAKE_TASKS)
    )
    meetings_router.clear_cache()


def test_transcript_endpoint_returns_scrubbed(client, monkeypatch):
    _install_mongo(monkeypatch)
    body = client.get(f"/api/v1/meetings/{_FAKE_TASK_ID}/transcript").json()
    assert body["id"] == str(_FAKE_TASK_ID)
    assert body["scrubbed"] is True
    text = body["transcription"]
    assert "Alex Synthetic" not in text
    assert "ABCDE1234F" not in text
    assert "9123456780" not in text
    assert "alex.synthetic@example.com" not in text
    assert "[CLIENT_NAME]" in text
    assert "[PAN]" in text
    assert "[PHONE]" in text
    assert "[EMAIL]" in text


def test_runs_with_meeting_id_uses_scrubbed_text(client, monkeypatch):
    _install_mongo(monkeypatch)
    seen = {}

    async def _fake_complete(*, model, system, user):
        seen["user"] = user
        return ({"ok": {"value": "1", "confidence": 1.0, "confidence_type": "quoted", "evidence": "e"}},
                {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10})

    monkeypatch.setattr(runs_router, "complete_json", _fake_complete)
    aid = client.post("/api/v1/agents", json={"name": "ScrubAgent", "prompt": "p"}).json()["id"]

    # meeting_id wins: junk client text + non-transcription type are ignored.
    run = client.post(
        "/api/v1/runs",
        json={
            "input_type": "mail",
            "input_data": "Alex Synthetic raw junk ABCDE1234F should be ignored",
            "meeting_id": str(_FAKE_TASK_ID),
            "agent_ids": [aid],
            "model": "test-model",
        },
    ).json()
    rid = run["id"]

    detail = client.get(f"/api/v1/runs/{rid}").json()
    assert detail["input_type"] == "transcription"
    stored = detail["input_data"]
    assert "Alex Synthetic" not in stored
    assert "ABCDE1234F" not in stored
    assert "alex.synthetic@example.com" not in stored
    assert "[CLIENT_NAME]" in stored
    assert "[PAN]" in stored
    # Whatever text was actually sent to the model is the scrubbed text.
    assert "[CLIENT_NAME]" in seen["user"] and "[PAN]" in seen["user"]
    assert "ABCDE1234F" not in seen["user"]

    # Log row mirrors the scrubbed input.
    logs = client.get("/api/v1/logs").json()
    assert logs[0]["input_data"] == stored


def test_runs_422_when_no_meeting_and_blank_input(client, monkeypatch):
    _install_mongo(monkeypatch)
    resp = client.post(
        "/api/v1/runs",
        json={"input_type": "mail", "input_data": "   ", "agent_ids": [], "model": "m"},
    )
    assert resp.status_code == 422
    # Unknown meeting still 404s.
    resp = client.post(
        "/api/v1/runs",
        json={"input_type": "mail", "input_data": "x", "meeting_id": str(ObjectId()),
              "agent_ids": [], "model": "m"},
    )
    assert resp.status_code == 404
