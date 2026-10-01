from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.openrouter import complete_json
from app.db.models import Agent, Attribute, Feedback, Run, RunLog
from app.db.session import get_db
from app.modules.runs.schemas import FeedbackCreate, RunCreate

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


def _agent_prompt(agent: Agent, attrs: list[Attribute], input_type: str, input_data: str) -> str:
    attr_lines = "\n".join(f"- {a.name} ({a.type}): {a.description}" for a in attrs) or "- (no attributes defined)"
    return f"""{agent.prompt}

Input type: {input_type}
Attributes to extract:
{attr_lines}

Return a JSON object keyed by attribute name. Each value must be an object with
"value", "confidence" (0-1), "confidence_type" (quoted|inferred|…), "evidence" (exact quote).

Input data:
{input_data}"""


@router.post("")
async def create_run(payload: RunCreate, db: Session = Depends(get_db)):
    if payload.input_type not in ("transcription", "messages", "mail"):
        raise HTTPException(422, "input_type must be transcription|messages|mail")
    agents = db.query(Agent).filter(Agent.id.in_(payload.agent_ids)).all() if payload.agent_ids else []
    if payload.agent_ids and not agents:
        raise HTTPException(404, "no matching agents")
    outputs: dict = {}
    snapshots: dict = {}
    for agent in agents:
        attrs = db.query(Attribute).filter(Attribute.agent_id == agent.id).all()
        snapshots[agent.id] = {
            "name": agent.name, "system_instruction": agent.system_instruction, "prompt": agent.prompt,
            "attributes": [{"name": a.name, "type": a.type, "description": a.description, "json_schema": a.json_schema or {}} for a in attrs],
        }
        try:
            outputs[agent.id] = await complete_json(
                model=payload.model, system=agent.system_instruction or "Extract structured data.",
                user=_agent_prompt(agent, attrs, payload.input_type, payload.input_data))
        except Exception as exc:
            outputs[agent.id] = {"_error": str(exc)}
    run = Run(input_type=payload.input_type, input_data=payload.input_data, model=payload.model,
              agent_ids=payload.agent_ids, outputs=outputs)
    db.add(run)
    db.commit()
    db.refresh(run)
    # Denormalized log row — snapshots only, no FK to agents/attributes.
    db.add(RunLog(run_id=run.id, input_type=run.input_type, input_data=run.input_data, model=run.model,
                  agent_snapshot=snapshots, attribute_snapshot=snapshots, outputs=outputs, feedback={}))
    db.commit()
    return {"id": run.id, "outputs": outputs}


@router.post("/{run_id}/feedback")
def add_feedback(run_id: str, payload: FeedbackCreate, db: Session = Depends(get_db)):
    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(404, "run not found")
    if payload.rating not in ("up", "down"):
        raise HTTPException(422, "rating must be up|down")
    db.add(Feedback(run_id=run_id, agent_name=payload.agent_name, attribute_name=payload.attribute_name,
                    rating=payload.rating, remarks=payload.remarks))
    log = db.query(RunLog).filter(RunLog.run_id == run_id).first()
    if log:
        fb = dict(log.feedback or {})
        fb.setdefault(payload.agent_name or "agent", {}).update(
            {payload.attribute_name or "attribute": {"rating": payload.rating, "remarks": payload.remarks}})
        log.feedback = fb
    db.commit()
    return {"ok": True}


@router.get("")
def list_runs(db: Session = Depends(get_db)):
    rows = db.query(Run).order_by(Run.created_at.desc()).limit(100).all()
    return [{"id": r.id, "input_type": r.input_type, "model": r.model, "agent_ids": r.agent_ids, "created_at": r.created_at} for r in rows]
