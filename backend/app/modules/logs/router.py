from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import or_
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


def _agent_id_for_name(log, agent_name: str) -> str | None:
    """Agent id for a feedback agent_name (snapshot lookup, never raises)."""
    try:
        name = (agent_name or "").strip()
        if not name:
            return None
        snap = getattr(log, "agent_snapshot", None) or {}
        if isinstance(snap, dict):
            for aid, s in snap.items():
                try:
                    if isinstance(s, dict) and (s.get("name", "") or "") == name:
                        return str(aid)
                except Exception:
                    continue
            # Fallback: outputs keyed by name (old logs)?
            outs = getattr(log, "outputs", None) or {}
            if isinstance(outs, dict) and name in outs:
                return name
    except Exception:
        return None
    return None


def _agent_name_for_id(log, agent_id: str, fallback: str = "") -> str:
    try:
        snap = getattr(log, "agent_snapshot", None) or {}
        if isinstance(snap, dict):
            s = snap.get(agent_id)
            if isinstance(s, dict) and isinstance(s.get("name"), str) and s.get("name"):
                return s.get("name")
    except Exception:
        pass
    return fallback or agent_id


def _reused_aids(log) -> set[str]:
    """Agent ids whose output was reused (per-agent + whole-log)."""
    out: set[str] = set()
    try:
        usage = getattr(log, "usage", None) or {}
        if isinstance(usage, dict):
            ra = usage.get("reused_agents", {}) or {}
            if isinstance(ra, dict):
                for aid in ra.keys():
                    if isinstance(aid, str) and aid:
                        out.add(aid)
    except Exception:
        pass
    try:
        if getattr(log, "reused_from_log_id", None):
            snap = getattr(log, "agent_snapshot", None) or {}
            if isinstance(snap, dict):
                for aid in snap.keys():
                    if isinstance(aid, str) and aid:
                        out.add(aid)
            else:
                outs = getattr(log, "outputs", None) or {}
                if isinstance(outs, dict):
                    for aid in outs.keys():
                        if isinstance(aid, str) and aid:
                            out.add(aid)
    except Exception:
        pass
    return out


def _source_log_id_for_agent(log, agent_id: str) -> str | None:
    try:
        usage = getattr(log, "usage", None) or {}
        if isinstance(usage, dict):
            ra = usage.get("reused_agents", {}) or {}
            if isinstance(ra, dict):
                ent = ra.get(agent_id)
                if isinstance(ent, dict):
                    lid = ent.get("log_id", "")
                    if isinstance(lid, str) and lid:
                        return lid
    except Exception:
        pass
    try:
        # Whole-log reuse: every agent comes from the single source.
        lid = getattr(log, "reused_from_log_id", None) or ""
        if isinstance(lid, str) and lid:
            return lid
    except Exception:
        pass
    return None


def _resolve_original_log(db, log, agent_id: str, cache=None, visited=None):
    """Follow per-agent reuse chains to the original log (cycle-guarded).

    Returns the original RunLog (or ``log`` itself when not reused / source
    missing). ``cache`` maps log_id -> RunLog to avoid refetching.
    """
    try:
        if cache is None:
            cache = {}
        if visited is None:
            visited = set()
        cur = log
        try:
            visited.add(getattr(cur, "id", ""))
        except Exception:
            pass
        for _ in range(20):
            try:
                aid = str(agent_id or "")
            except Exception:
                break
            if not aid:
                break
            src_id = _source_log_id_for_agent(cur, aid)
            if not src_id or src_id in visited:
                break
            visited.add(src_id)
            try:
                if src_id in cache:
                    src = cache[src_id]
                else:
                    src = db.query(RunLog).filter(RunLog.id == src_id).first()
                    cache[src_id] = src
            except Exception:
                break
            if src is None:
                break
            cur = src
        return cur
    except Exception:
        return log


def _merged_feedback(db, log, cache=None) -> dict:
    """API-facing feedback: source feedback for reused agents (merged).

    - For each reused agent, the returned cells are the ORIGINAL log's
      feedback (chain-resolved, cycle-guarded), each cell with
      ``source_log_id`` marker. The new row's own cells for those agents are
      ignored (legacy rows that already hold a copy).
    - Non-reused agents return the row's own feedback unchanged.
    - Never writes to the DB.
    """
    try:
        import copy as _copy
        own = getattr(log, "feedback", None) or {}
        base = _copy.deepcopy(own) if isinstance(own, dict) else {}
        if not isinstance(base, dict):
            base = {}
    except Exception:
        base = {}
    try:
        aids = _reused_aids(log)
    except Exception:
        return base
    if not aids:
        return base
    if cache is None:
        cache = {}
    for aid in aids:
        try:
            orig = _resolve_original_log(db, log, aid, cache=cache, visited=None)
        except Exception:
            continue
        try:
            orig_id = getattr(orig, "id", "") or ""
            is_self = orig_id == getattr(log, "id", "")
        except Exception:
            is_self = True
        if is_self:
            continue
        try:
            agent_name = _agent_name_for_id(log, aid, fallback="")
            if not agent_name:
                continue
            # Source may key by its own snapshot name for the same id.
            src_name = _agent_name_for_id(orig, aid, fallback=agent_name)
            src_fb = getattr(orig, "feedback", None) or {}
            if not isinstance(src_fb, dict):
                src_fb = {}
            src_cells = src_fb.get(src_name, {}) if isinstance(src_fb, dict) else {}
            if not isinstance(src_cells, dict):
                src_cells = {}
            # Legacy: ignore the new row's own cells for reused agents.
            import copy as _copy2
            merged: dict = {}
            for attr, cell in src_cells.items():
                if not isinstance(cell, dict):
                    continue
                try:
                    c = dict(cell)
                except Exception:
                    continue
                c["source_log_id"] = orig_id
                merged[attr] = c
            if agent_name in base:
                try:
                    del base[agent_name]
                except Exception:
                    pass
            if merged:
                base[agent_name] = merged
        except Exception:
            continue
    return base


def _out(r: RunLog, feedback_override=None) -> dict:
    reused_at = getattr(r, "reused_from_created_at", None)
    if isinstance(reused_at, datetime):
        reused_at_out = reused_at.isoformat()
    elif isinstance(reused_at, str):
        reused_at_out = reused_at or None
    else:
        reused_at_out = None
    consistency = getattr(r, "consistency", None)
    if not isinstance(consistency, dict):
        consistency = {}
    try:
        fb = feedback_override if isinstance(feedback_override, dict) else (r.feedback or {})
    except Exception:
        fb = r.feedback or {}
    return {"id": r.id, "run_id": r.run_id, "input_type": r.input_type, "input_data": r.input_data,
            "model": r.model,
            "agent_snapshot": r.agent_snapshot or {}, "attribute_snapshot": r.attribute_snapshot or {},
            "outputs": r.outputs or {}, "feedback": fb,
            "consistency": consistency,
            "usage": getattr(r, "usage", None) or {}, "filters": getattr(r, "filters", None) or {},
            "requests": getattr(r, "requests", None) or {},
            # Denormalized meeting snapshot (plain strings, "" on old rows).
            "client": getattr(r, "client", None) or "",
            "meeting_type": getattr(r, "meeting_type", None) or "",
            "meeting_title": getattr(r, "meeting_title", None) or "",
            "reasoning_effort": getattr(r, "reasoning_effort", None) or "",
            "provider": getattr(r, "provider", None) or "",
            "run_group_id": getattr(r, "run_group_id", None) or "",
            "reused_from_log_id": getattr(r, "reused_from_log_id", None) or "",
            "reused_from_created_at": reused_at_out,
            "created_at": r.created_at}


def _out_resolved(r: RunLog, db, cache=None) -> dict:
    """_out with source feedback merged for reused agents (read-only)."""
    try:
        fb = _merged_feedback(db, r, cache=cache)
    except Exception:
        fb = None
    if fb is None:
        return _out(r)
    return _out(r, feedback_override=fb)


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
    reasoning_efforts: list[str] | None = Query(default=None),
    run_group_ids: list[str] | None = Query(default=None),
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
    wanted_efforts = _as_list(
        reasoning_efforts, qp.getlist("reasoning_efforts[]"),
        qp.getlist("reasoning_effort"), qp.getlist("reasoning_effort[]"),
    )
    wanted_groups = _as_list(
        run_group_ids, qp.getlist("run_group_ids[]"),
        qp.getlist("run_group_id"), qp.getlist("run_group_id[]"),
    )
    model_set = set(wanted_models)
    agent_set = set(wanted_agents)
    date_set = set(wanted_dates)
    client_set = set(wanted_clients)
    type_set = set(wanted_types)
    title_set = set(wanted_titles)
    effort_set = set(wanted_efforts)

    q = db.query(RunLog)
    if wanted_groups:
        ids = list(dict.fromkeys(wanted_groups))
        q = q.filter(or_(RunLog.run_group_id.in_(ids), RunLog.id.in_(ids)))
    rows = q.order_by(RunLog.created_at.desc()).limit(500).all()
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
    if effort_set:
        rows = [r for r in rows if (getattr(r, "reasoning_effort", None) or "") in effort_set]
    cache: dict = {}
    return [_out_resolved(r, db, cache=cache) for r in rows[:300]]


@router.get("/{log_id}")
def get_log(log_id: str, db: Session = Depends(get_db)):
    r = db.query(RunLog).filter(RunLog.id == log_id).first()
    if not r:
        raise HTTPException(404, "log not found")
    return _out_resolved(r, db)
