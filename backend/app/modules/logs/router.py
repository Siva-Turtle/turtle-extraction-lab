from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.db.models import RunLog
from app.db.session import get_db

router = APIRouter(prefix="/api/v1/logs", tags=["logs"])

IST = ZoneInfo("Asia/Kolkata")


def _as_list(*vals) -> list[str]:
    """Flatten repeat query params into a cleaned list (ignores blanks)."""
    out: list[str] = []
    for v in vals:
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            out.extend(_as_list(*v))
        elif isinstance(v, str):
            s = v.strip()
            if s:
                out.append(s)
    # de-dupe, keep order
    return list(dict.fromkeys(out))


def _log_date(value) -> str | None:
    """YYYY-MM-DD of a log timestamp in Asia/Kolkata (matches the UI)."""
    try:
        if isinstance(value, str):
            s = value.strip()
            if not s:
                return None
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        elif isinstance(value, datetime):
            dt = value
        else:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(IST).date().isoformat()
    except Exception:
        return None


def _out(r: RunLog) -> dict:
    return {"id": r.id, "run_id": r.run_id, "input_type": r.input_type, "input_data": r.input_data,
            "model": r.model,
            "agent_snapshot": r.agent_snapshot or {}, "attribute_snapshot": r.attribute_snapshot or {},
            "outputs": r.outputs or {}, "feedback": r.feedback or {},
            "usage": getattr(r, "usage", None) or {}, "filters": getattr(r, "filters", None) or {},
            "requests": getattr(r, "requests", None) or {},
            # Denormalized meeting snapshot (plain strings, "" on old rows).
            "client": getattr(r, "client", None) or "",
            "meeting_type": getattr(r, "meeting_type", None) or "",
            "meeting_title": getattr(r, "meeting_title", None) or "",
            "created_at": r.created_at}


@router.get("")
def list_logs(
    request: Request,
    db: Session = Depends(get_db),
    models: list[str] | None = Query(default=None),
    agent_ids: list[str] | None = Query(default=None),
    dates: list[str] | None = Query(default=None),
    clients: list[str] | None = Query(default=None),
    meeting_types: list[str] | None = Query(default=None),
    meeting_titles: list[str] | None = Query(default=None),
):
    # Accept both `models=a&models=b` and axios-style `models[]=a&models[]=b`.
    qp = request.query_params
    wanted_models = _as_list(
        models, qp.getlist("models[]"), qp.getlist("model"), qp.getlist("model[]"),
    )
    wanted_agents = _as_list(
        agent_ids, qp.getlist("agent_ids[]"),
        qp.getlist("agent_id"), qp.getlist("agent_id[]"),
        qp.getlist("agents"), qp.getlist("agents[]"),
    )
    wanted_dates = _as_list(
        dates, qp.getlist("dates[]"), qp.getlist("date"), qp.getlist("date[]"),
    )
    wanted_clients = _as_list(
        clients, qp.getlist("clients[]"), qp.getlist("client"), qp.getlist("client[]"),
    )
    wanted_types = _as_list(
        meeting_types, qp.getlist("meeting_types[]"),
        qp.getlist("meeting_type"), qp.getlist("meeting_type[]"),
    )
    wanted_titles = _as_list(
        meeting_titles, qp.getlist("meeting_titles[]"),
        qp.getlist("meeting_title"), qp.getlist("meeting_title[]"),
    )
    model_set = set(wanted_models)
    agent_set = set(wanted_agents)
    date_set = set(wanted_dates)
    client_set = set(wanted_clients)
    type_set = set(wanted_types)
    title_set = set(wanted_titles)

    rows = db.query(RunLog).order_by(RunLog.created_at.desc()).limit(500).all()
    if model_set:
        rows = [r for r in rows if (r.model or "") in model_set]
    if agent_set:
        rows = [
            r for r in rows
            if isinstance(r.agent_snapshot, dict)
            and any(a in r.agent_snapshot for a in agent_set)
        ]
    if date_set:
        rows = [r for r in rows if _log_date(r.created_at) in date_set]
    if client_set:
        rows = [r for r in rows if (getattr(r, "client", None) or "") in client_set]
    if type_set:
        rows = [r for r in rows if (getattr(r, "meeting_type", None) or "") in type_set]
    if title_set:
        rows = [r for r in rows if (getattr(r, "meeting_title", None) or "") in title_set]
    return [_out(r) for r in rows[:100]]


@router.get("/{log_id}")
def get_log(log_id: str, db: Session = Depends(get_db)):
    r = db.query(RunLog).filter(RunLog.id == log_id).first()
    if not r:
        raise HTTPException(404, "log not found")
    return _out(r)
