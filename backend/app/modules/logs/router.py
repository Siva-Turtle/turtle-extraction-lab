from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.models import RunLog
from app.db.session import get_db

router = APIRouter(prefix="/api/v1/logs", tags=["logs"])


def _out(r: RunLog) -> dict:
    return {"id": r.id, "run_id": r.run_id, "input_type": r.input_type, "input_data": r.input_data,
            "model": r.model,
            "agent_snapshot": r.agent_snapshot or {}, "attribute_snapshot": r.attribute_snapshot or {},
            "outputs": r.outputs or {}, "feedback": r.feedback or {},
            "usage": getattr(r, "usage", None) or {}, "filters": getattr(r, "filters", None) or {},
            "requests": getattr(r, "requests", None) or {},
            "created_at": r.created_at}


@router.get("")
def list_logs(db: Session = Depends(get_db)):
    rows = db.query(RunLog).order_by(RunLog.created_at.desc()).limit(100).all()
    return [_out(r) for r in rows]


@router.get("/{log_id}")
def get_log(log_id: str, db: Session = Depends(get_db)):
    r = db.query(RunLog).filter(RunLog.id == log_id).first()
    if not r:
        raise HTTPException(404, "log not found")
    return _out(r)
