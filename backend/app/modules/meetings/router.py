"""Meetings proxy: Mongo clients + Fireflies transcripts.

Browser never touches Mongo/Fireflies directly — this backend proxies everything.
Read-only Mongo discipline: only find/find_one (no writes, ever).
Secrets come from environment via app.core.config.settings only; never log them.
"""

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.core.config import settings
from app.modules.meetings.schemas import (
    ClientOut,
    MeetingsOut,
    MeetingOut,
    TitlesOut,
    TranscriptOut,
)

router = APIRouter(prefix="/api/v1/meetings", tags=["meetings"])

MONGO_DB = "turtle-finance-db"
MONGO_COLLECTION = "clients"

CACHE_TTL_SECONDS = 300
_CACHE: dict[str, tuple[float, Any]] = {}

_IST = timezone(timedelta(hours=5, minutes=30))

LIST_QUERY = """
query Transcripts($limit: Int, $fromDate: String, $toDate: String) {
  transcripts(limit: $limit, fromDate: $fromDate, toDate: $toDate) {
    id
    title
    date
    duration
    meeting_link
    participants
    organizer_email
    meeting_attendees {
      displayName
      email
    }
  }
}
"""

DETAIL_QUERY = """
query TranscriptDetail($id: String!) {
  transcript(id: $id) {
    id
    title
    date
    sentences {
      speaker_name
      text
    }
  }
}
"""


def clear_cache() -> None:
    _CACHE.clear()


def _cache_get(key: str) -> Any | None:
    hit = _CACHE.get(key)
    if not hit:
        return None
    expires_at, value = hit
    if time.time() > expires_at:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: Any) -> None:
    _CACHE[key] = (time.time() + CACHE_TTL_SECONDS, value)


def build_transcription(sentences: list[dict] | None) -> str:
    """Join Fireflies sentences into "Speaker: line" text."""
    lines: list[str] = []
    for s in sentences or []:
        if not isinstance(s, dict):
            continue
        speaker = (s.get("speaker_name") or "Speaker") or "Speaker"
        text = (s.get("text") or "")
        if isinstance(text, str):
            text = text.strip()
        else:
            text = str(text).strip()
        if not text:
            continue
        lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


def _mongo_503() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="MongoDB unavailable: set MONGODB_URI in backend/.env",
    )


def _fireflies_503() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="Fireflies unavailable: set FIREFLIES_API_KEY in backend/.env",
    )


def _require_mongo_uri() -> str:
    uri = (settings.mongodb_uri or "").strip()
    if not uri:
        raise _mongo_503()
    return uri


def _require_fireflies_key() -> str:
    key = (settings.fireflies_api_key or "").strip()
    if not key:
        raise _fireflies_503()
    return key


def _mongo_client(uri: str):
    from pymongo import MongoClient

    return MongoClient(uri, serverSelectionTimeoutMS=5000)


def _fetch_mongo_docs() -> list[dict]:
    uri = _require_mongo_uri()
    try:
        client = _mongo_client(uri)
    except Exception:
        raise _mongo_503()
    try:
        coll = client[MONGO_DB][MONGO_COLLECTION]
        # Read-only: find only.
        return list(coll.find({}, {"clientId": 1, "fullName": 1, "email": 1}))
    except Exception:
        raise _mongo_503()
    finally:
        try:
            client.close()
        except Exception:
            pass


def _fetch_mongo_client_doc(client_id: str) -> dict | None:
    uri = _require_mongo_uri()
    try:
        client = _mongo_client(uri)
    except Exception:
        raise _mongo_503()
    try:
        coll = client[MONGO_DB][MONGO_COLLECTION]
        doc = coll.find_one({"clientId": client_id}, {"clientId": 1, "fullName": 1, "email": 1})
        if doc is not None:
            return doc
        # Fallback: _id match (str form of ObjectId).
        try:
            from bson import ObjectId

            try:
                oid = ObjectId(client_id)
            except Exception:
                oid = None
            if oid is not None:
                return coll.find_one({"_id": oid}, {"clientId": 1, "fullName": 1, "email": 1})
            return coll.find_one({"_id": client_id}, {"clientId": 1, "fullName": 1, "email": 1})
        except Exception:
            try:
                return coll.find_one({"_id": client_id}, {"clientId": 1, "fullName": 1, "email": 1})
            except Exception:
                raise _mongo_503()
    except HTTPException:
        raise
    except Exception:
        raise _mongo_503()
    finally:
        try:
            client.close()
        except Exception:
            pass


def _emails_from_doc(doc: dict) -> set[str]:
    raw = doc.get("email", [])
    if isinstance(raw, str):
        raw = [raw]
    elif not isinstance(raw, list):
        raw = []
    out: set[str] = set()
    for e in raw:
        if isinstance(e, str) and e.strip():
            out.add(e.strip().lower())
        elif isinstance(e, dict) and e.get("email"):
            v = str(e["email"]).strip().lower()
            if v:
                out.add(v)
    return out


def _extract_participants(t: dict) -> list[str]:
    parts: list[str] = []

    def _push(v: Any) -> None:
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
        elif isinstance(v, dict):
            email = v.get("email")
            if isinstance(email, str) and email.strip():
                parts.append(email.strip())

    for p in t.get("participants") or []:
        _push(p)
    org = t.get("organizer_email")
    if isinstance(org, str) and org.strip():
        parts.append(org.strip())
    for a in t.get("meeting_attendees") or []:
        _push(a)
    # Deduplicate preserving order.
    seen: set[str] = set()
    uniq: list[str] = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def _participant_emails_lower(t: dict) -> set[str]:
    return {p.lower() for p in _extract_participants(t) if isinstance(p, str)}


def _matches_client(t: dict, emails_lower: set[str] | None, name_lower: str | None) -> bool:
    """Client match: email in participants OR name substring in title (case-insensitive)."""
    if emails_lower is None and not name_lower:
        return True
    title = t.get("title") or ""
    title_lower = title.lower() if isinstance(title, str) else str(title).lower()
    if name_lower and name_lower in title_lower:
        return True
    if emails_lower:
        if _participant_emails_lower(t) & emails_lower:
            return True
    return False


def _resolve_client_filter(client_id: str | None) -> tuple[set[str] | None, str | None, bool]:
    """Return (emails_lower|None, name_lower|None, no_match). no_match=True when doc missing."""
    if not client_id:
        return None, None, False
    doc = _fetch_mongo_client_doc(client_id)
    if not doc:
        return set(), "", True
    emails = _emails_from_doc(doc)
    name = doc.get("fullName") or ""
    name_lower = name.strip().lower() if isinstance(name, str) else str(name).strip().lower()
    return emails, name_lower, False


def _fireflies_post(query: str, variables: dict) -> dict:
    key = _require_fireflies_key()
    url = (settings.fireflies_api_url or "https://api.fireflies.ai/graphql").strip() or "https://api.fireflies.ai/graphql"
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(
                url,
                headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                json={"query": query, "variables": variables},
            )
            resp.raise_for_status()
            return resp.json()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail="Fireflies request failed")


def _fetch_transcripts_list(*, limit: int = 200, from_date: str | None = None,
                            to_date: str | None = None) -> list[dict]:
    _require_fireflies_key()
    cache_key = f"transcripts:{limit}:{from_date}:{to_date}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    variables: dict[str, Any] = {"limit": limit}
    if from_date is not None:
        variables["fromDate"] = from_date
    if to_date is not None:
        variables["toDate"] = to_date
    data = _fireflies_post(LIST_QUERY, variables)
    items = ((data.get("data") or {}).get("transcripts")) or []
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        items = []
    result = [t for t in items if isinstance(t, dict)]
    _cache_set(cache_key, result)
    return result


def _fetch_transcript_detail(meeting_id: str) -> dict | None:
    _require_fireflies_key()
    data = _fireflies_post(DETAIL_QUERY, {"id": meeting_id})
    t = (data.get("data") or {}).get("transcript")
    return t if isinstance(t, dict) else None


def _ist_day_bounds(date_str: str) -> tuple[str, str]:
    try:
        day = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")
    start_ist = datetime(day.year, day.month, day.day, tzinfo=_IST)
    end_ist = start_ist + timedelta(days=1)
    start_utc = start_ist.astimezone(timezone.utc)
    end_utc = end_ist.astimezone(timezone.utc)
    return start_utc.isoformat(), end_utc.isoformat()


def _parse_meeting_dt(value: Any) -> datetime | None:
    try:
        if value is None or value == "":
            return None
        if isinstance(value, (int, float)):
            ts = float(value)
            if ts > 1e11:  # epoch ms
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        if isinstance(value, str):
            s = value.strip()
            if not s:
                return None
            # Epoch as string?
            try:
                num = float(s)
                if num > 1e8:
                    ts = num / 1000.0 if num > 1e11 else num
                    return datetime.fromtimestamp(ts, tz=timezone.utc)
            except ValueError:
                pass
            iso = s.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(iso)
            except ValueError:
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        return None
    except Exception:
        return None


def _to_iso_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        dt = _parse_meeting_dt(value)
        return dt.isoformat() if dt else str(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return ""
        dt = _parse_meeting_dt(s)
        # Keep original string when it already looks ISO; normalize epoch strings.
        if dt is not None:
            try:
                float(s)
                return dt.isoformat()
            except ValueError:
                return s
        return s
    return str(value)


def _to_duration_min(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _meeting_ist_date(value: Any) -> str | None:
    dt = _parse_meeting_dt(value)
    if dt is None:
        return None
    return dt.astimezone(_IST).strftime("%Y-%m-%d")


@router.get("/clients", response_model=list[ClientOut])
def list_clients():
    docs = _fetch_mongo_docs()
    out: list[ClientOut] = []
    for d in docs:
        if not isinstance(d, dict):
            continue
        name = d.get("fullName") or ""
        if isinstance(name, str):
            name = name.strip()
        else:
            name = str(name).strip()
        if not name:
            continue
        cid = d.get("clientId") or str(d.get("_id", ""))
        cid = str(cid).strip()
        if not cid:
            continue
        out.append(ClientOut(id=cid, name=name))
    out.sort(key=lambda c: c.name.lower())
    return out


@router.get("/titles", response_model=TitlesOut)
def list_titles(client_id: str | None = Query(default=None)):
    _require_fireflies_key()
    if client_id:
        emails, name_lower, no_match = _resolve_client_filter(client_id)
        if no_match:
            return TitlesOut(titles=[])
    else:
        emails, name_lower = None, None
    items = _fetch_transcripts_list(limit=200)
    titles: set[str] = set()
    for t in items:
        if client_id and not _matches_client(t, emails, name_lower):
            continue
        title = t.get("title")
        if isinstance(title, str) and title.strip():
            titles.add(title.strip())
    return TitlesOut(titles=sorted(titles, key=lambda s: s.lower()))


@router.get("", response_model=MeetingsOut)
def list_meetings(
    client_id: str | None = Query(default=None),
    title: str | None = Query(default=None),
    date: str | None = Query(default=None),
):
    _require_fireflies_key()
    emails: set[str] | None = None
    name_lower: str | None = None
    if client_id:
        emails, name_lower, no_match = _resolve_client_filter(client_id)
        if no_match:
            return MeetingsOut(meetings=[])
    if date:
        from_date, to_date = _ist_day_bounds(date)
        items = _fetch_transcripts_list(limit=200, from_date=from_date, to_date=to_date)
    else:
        items = _fetch_transcripts_list(limit=200)
    out: list[MeetingOut] = []
    for t in items:
        if client_id and not _matches_client(t, emails, name_lower):
            continue
        if title and (t.get("title") or "") != title:
            continue
        if date:
            ist_day = _meeting_ist_date(t.get("date"))
            if ist_day != date:
                continue
        mid = str(t.get("id") or "")
        if not mid:
            continue
        mtitle = t.get("title") or ""
        out.append(MeetingOut(
            id=mid,
            title=str(mtitle),
            date=_to_iso_string(t.get("date")),
            duration_min=_to_duration_min(t.get("duration")),
            participants=_extract_participants(t),
        ))
    return MeetingsOut(meetings=out)


@router.get("/{meeting_id}/transcript", response_model=TranscriptOut)
def get_transcript(meeting_id: str):
    _require_fireflies_key()
    t = _fetch_transcript_detail(meeting_id)
    if not t:
        raise HTTPException(status_code=404, detail="transcript not found")
    sentences = t.get("sentences")
    if not sentences:
        raise HTTPException(status_code=404, detail="transcript not found")
    text = build_transcription(sentences if isinstance(sentences, list) else [])
    if not text:
        raise HTTPException(status_code=404, detail="transcript not found")
    return TranscriptOut(
        id=str(t.get("id") or meeting_id),
        title=str(t.get("title") or ""),
        date=_to_iso_string(t.get("date")),
        transcription=text,
    )
