"""Meetings proxy: Mongo clients + Mongo task transcripts.

Browser never touches Mongo directly — this backend proxies everything.
Read-only Mongo discipline: only find/find_one (no writes, ever).
Secrets come from environment via app.core.config.settings only; never log them.
"""

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.core.config import settings
from app.core.scrub import mask_for_classifier
from app.modules.meetings.schemas import (
    ClientOut,
    MeetingsOut,
    MeetingOut,
    TitlesOut,
    TranscriptOut,
)

router = APIRouter(prefix="/api/v1/meetings", tags=["meetings"])

MONGO_DB = "turtle-finance-db"
MONGO_CLIENTS_COLLECTION = "clients"
MONGO_TASKS_COLLECTION = "tasks"

CACHE_TTL_SECONDS = 300
_CACHE: dict[str, tuple[float, Any]] = {}

_IST = timezone(timedelta(hours=5, minutes=30))

TITLES_RECENT_LIMIT = 500
MEETINGS_RETURN_CAP = 200

TASK_LIST_PROJECTION = {
    "title": 1,
    "date": 1,
    "createdAt": 1,
    "client": 1,
    "participants": 1,
    "duration": 1,
    "duration_min": 1,
    "durationMin": 1,
    "durationMinutes": 1,
}


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


def _mongo_503() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="MongoDB unavailable: set MONGODB_URI in backend/.env",
    )


def _require_mongo_uri() -> str:
    uri = (settings.mongodb_uri or "").strip()
    if not uri:
        raise _mongo_503()
    return uri


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
        coll = client[MONGO_DB][MONGO_CLIENTS_COLLECTION]
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
        coll = client[MONGO_DB][MONGO_CLIENTS_COLLECTION]
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


def _find_tasks(filt: dict, proj: dict | None = None) -> list[dict]:
    """Read-only task fetch. Sorting/caps happen in Python (tolerant date parsing)."""
    uri = _require_mongo_uri()
    try:
        client = _mongo_client(uri)
    except Exception:
        raise _mongo_503()
    try:
        coll = client[MONGO_DB][MONGO_TASKS_COLLECTION]
        # Read-only: find only.
        docs = list(coll.find(filt, proj))
        return [d for d in docs if isinstance(d, dict)]
    except HTTPException:
        raise
    except Exception:
        raise _mongo_503()
    finally:
        try:
            client.close()
        except Exception:
            pass


def _fetch_task_by_id(meeting_id: str) -> dict | None:
    uri = _require_mongo_uri()
    try:
        client = _mongo_client(uri)
    except Exception:
        raise _mongo_503()
    try:
        coll = client[MONGO_DB][MONGO_TASKS_COLLECTION]
        key: Any = meeting_id
        try:
            from bson import ObjectId

            try:
                key = ObjectId(meeting_id)
            except Exception:
                key = meeting_id
        except Exception:
            key = meeting_id
        # Read-only: find_one only.
        doc = coll.find_one({"_id": key})
        if doc is not None:
            return doc if isinstance(doc, dict) else None
        if key != meeting_id:
            try:
                fallback = coll.find_one({"_id": meeting_id})
            except Exception:
                return None
            return fallback if isinstance(fallback, dict) else None
        return None
    except HTTPException:
        raise
    except Exception:
        raise _mongo_503()
    finally:
        try:
            client.close()
        except Exception:
            pass


def _fetch_client_doc_by_ref(ref: Any) -> dict | None:
    """Fetch the FULL clients doc for a task's `client` ref (ObjectId or str).

    Unlike _fetch_mongo_client_doc (projected lookup by clientId), this
    resolves the task -> client link and returns the whole doc so the scrub
    layer can use its name/phone/email fields. Read-only: find_one only.
    """
    uri = _require_mongo_uri()
    try:
        client = _mongo_client(uri)
    except Exception:
        raise _mongo_503()
    try:
        coll = client[MONGO_DB][MONGO_CLIENTS_COLLECTION]
        candidates: list = []
        if ref is not None and ref != "":
            candidates.append(ref)
            s = str(ref)
            if not any(c == s for c in candidates):
                candidates.append(s)
            try:
                from bson import ObjectId

                try:
                    oid = ref if isinstance(ref, ObjectId) else ObjectId(s)
                except Exception:
                    oid = None
                if oid is not None and not any(c == oid for c in candidates):
                    candidates.append(oid)
            except Exception:
                pass
        for cand in candidates:
            try:
                doc = coll.find_one({"_id": cand})
            except Exception:
                continue
            if isinstance(doc, dict):
                return doc
        return None
    except HTTPException:
        raise
    except Exception:
        raise _mongo_503()
    finally:
        try:
            client.close()
        except Exception:
            pass


def get_scrubbed_transcript(meeting_id: str) -> tuple[dict, str]:
    """Shared helper: task's transcript text, PII-scrubbed. Raises 404/503.

    Chain: fullTranscript -> detailedNotes -> summary. The task's client doc
    (tasks.client ObjectId/str -> clients doc) feeds the known-contact pass;
    unknown/absent clients fall back to the generic pass only. Raw text must
    never leave the backend — both the transcript endpoint and runs use this.
    """
    task = _fetch_task_by_id(meeting_id)
    if not task:
        raise HTTPException(status_code=404, detail="transcript not found")
    text: str | None = None
    for key in ("fullTranscript", "detailedNotes", "summary"):
        v = task.get(key)
        if isinstance(v, str) and v.strip():
            text = v
            break
    if text is None:
        raise HTTPException(status_code=404, detail="transcript not found")
    client_doc: dict | None = None
    ref = task.get("client")
    if ref is not None and ref != "":
        try:
            client_doc = _fetch_client_doc_by_ref(ref)
        except HTTPException:
            raise
        except Exception:
            client_doc = None
    return task, mask_for_classifier(text, client_doc)


def _client_task_ids(doc: dict) -> list:
    """Match tasks.client against BOTH ObjectId and str forms of the client's _id."""
    raw = doc.get("_id")
    ids: list = []
    for cand in (raw, str(raw) if raw is not None else None):
        if cand is None or cand == "":
            continue
        if not any(c == cand for c in ids):
            ids.append(cand)
    try:
        from bson import ObjectId

        oid = raw if isinstance(raw, ObjectId) else ObjectId(str(raw))
        if not any(c == oid for c in ids):
            ids.append(oid)
    except Exception:
        pass
    return ids


def _validate_ist_day(date_str: str) -> None:
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")


def _parse_meeting_dt(value: Any) -> datetime | None:
    try:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            dt = value
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
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
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
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


def _task_raw_date(task: dict) -> Any:
    raw = task.get("date")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raw = task.get("createdAt")
    return raw


def _task_date_iso(task: dict) -> str:
    return _to_iso_string(_task_raw_date(task))


def _task_sort_key(task: dict) -> float:
    dt = _parse_meeting_dt(_task_raw_date(task))
    return dt.timestamp() if dt else float("-inf")


def _meeting_ist_date(value: Any) -> str | None:
    dt = _parse_meeting_dt(value)
    if dt is None:
        return None
    return dt.astimezone(_IST).strftime("%Y-%m-%d")


def _task_ist_day(task: dict) -> str | None:
    return _meeting_ist_date(_task_raw_date(task))


def _task_participants(task: dict) -> list[str]:
    raw = task.get("participants")
    if not isinstance(raw, list):
        return []
    parts: list[str] = []
    for p in raw:
        if isinstance(p, str):
            if p.strip():
                parts.append(p.strip())
        elif isinstance(p, dict):
            for key in ("email", "displayName", "name"):
                v = p.get(key)
                if isinstance(v, str) and v.strip():
                    parts.append(v.strip())
                    break
    # Deduplicate preserving order.
    seen: set[str] = set()
    uniq: list[str] = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def _task_duration_min(task: dict) -> float | None:
    for key in ("duration", "duration_min", "durationMin", "durationMinutes"):
        if key in task and task.get(key) is not None:
            return _to_duration_min(task.get(key))
    return None


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
    _require_mongo_uri()
    cache_key = f"titles:{client_id or ''}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return TitlesOut(titles=cached)
    if client_id:
        doc = _fetch_mongo_client_doc(client_id)
        if not doc:
            _cache_set(cache_key, [])
            return TitlesOut(titles=[])
        docs = _find_tasks(
            {"client": {"$in": _client_task_ids(doc)}},
            {"title": 1, "client": 1},
        )
        titles = _sorted_distinct_titles(docs)
    else:
        docs = _find_tasks({}, {"title": 1, "date": 1, "createdAt": 1})
        docs.sort(key=_task_sort_key, reverse=True)
        docs = docs[:TITLES_RECENT_LIMIT]
        titles = _sorted_distinct_titles(docs)
    _cache_set(cache_key, titles)
    return TitlesOut(titles=titles)


def _sorted_distinct_titles(docs: list[dict]) -> list[str]:
    titles: set[str] = set()
    for d in docs:
        title = d.get("title")
        if isinstance(title, str) and title.strip():
            titles.add(title.strip())
    return sorted(titles, key=lambda s: s.lower())


@router.get("", response_model=MeetingsOut)
def list_meetings(
    client_id: str | None = Query(default=None),
    title: str | None = Query(default=None),
    date: str | None = Query(default=None),
):
    _require_mongo_uri()
    if date:
        _validate_ist_day(date)
    cache_key = f"meetings:{client_id or ''}:{title or ''}:{date or ''}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return MeetingsOut(meetings=cached)
    filt: dict[str, Any] = {}
    if client_id:
        doc = _fetch_mongo_client_doc(client_id)
        if not doc:
            _cache_set(cache_key, [])
            return MeetingsOut(meetings=[])
        filt["client"] = {"$in": _client_task_ids(doc)}
    if title:
        filt["title"] = title
    docs = _find_tasks(filt, TASK_LIST_PROJECTION)
    if date:
        docs = [d for d in docs if _task_ist_day(d) == date]
    docs.sort(key=_task_sort_key, reverse=True)
    docs = docs[:MEETINGS_RETURN_CAP]
    out: list[MeetingOut] = []
    for d in docs:
        mid = str(d.get("_id", ""))
        if not mid:
            continue
        out.append(MeetingOut(
            id=mid,
            title=str(d.get("title") or ""),
            date=_task_date_iso(d),
            duration_min=_task_duration_min(d),
            participants=_task_participants(d),
        ))
    _cache_set(cache_key, out)
    return MeetingsOut(meetings=out)


@router.get("/{meeting_id}/transcript", response_model=TranscriptOut)
def get_transcript(meeting_id: str):
    _require_mongo_uri()
    task, scrubbed = get_scrubbed_transcript(meeting_id)
    return TranscriptOut(
        id=str(task.get("_id") or meeting_id),
        title=str(task.get("title") or ""),
        date=_task_date_iso(task),
        transcription=scrubbed,
        scrubbed=True,
    )
