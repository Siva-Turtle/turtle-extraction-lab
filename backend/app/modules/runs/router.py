from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.openrouter import complete_json, get_model_pricing
from app.db.models import Agent, Attribute, Feedback, Run, RunLog
from app.db.session import get_db
from app.modules.meetings.router import get_scrubbed_transcript
from app.modules.runs.schemas import FeedbackCreate, FeedbackOut, RunCreate, RunDetail, RunOut

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
    meeting_id = (payload.meeting_id or "").strip()
    if meeting_id:
        # meeting_id wins: server re-fetches the transcript and scrubs it —
        # client-sent input_data/input_type are ignored entirely.
        _task, scrubbed = get_scrubbed_transcript(meeting_id)  # 404/503 propagate
        input_type, input_data = "transcription", scrubbed
    else:
        if not (payload.input_data or "").strip():
            raise HTTPException(422, "input_data must be non-blank or provide meeting_id")
        if payload.input_type not in ("transcription", "messages", "mail"):
            raise HTTPException(422, "input_type must be transcription|messages|mail")
        input_type, input_data = payload.input_type, payload.input_data
    filters = payload.filters if isinstance(payload.filters, dict) else {}
    agents = db.query(Agent).filter(Agent.id.in_(payload.agent_ids)).all() if payload.agent_ids else []
    if payload.agent_ids and not agents:
        raise HTTPException(404, "no matching agents")
    wall_start = perf_counter()
    outputs: dict = {}
    snapshots: dict = {}
    per_agent: dict = {}
    total_in = 0
    total_out = 0
    for agent in agents:
        attrs = db.query(Attribute).filter(Attribute.agent_id == agent.id).all()
        snapshots[agent.id] = {
            "name": agent.name, "system_instruction": agent.system_instruction, "prompt": agent.prompt,
            "attributes": [{"name": a.name, "type": a.type, "description": a.description, "json_schema": a.json_schema or {}} for a in attrs],
        }
        agent_start = perf_counter()
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        try:
            parsed, usage = await complete_json(
                model=payload.model, system=agent.system_instruction or "Extract structured data.",
                user=_agent_prompt(agent, attrs, input_type, input_data))
            outputs[agent.id] = parsed
            try:
                prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
                completion_tokens = int(usage.get("completion_tokens", 0) or 0)
                total_tokens = int(usage.get("total_tokens", 0) or 0)
            except Exception:
                prompt_tokens, completion_tokens, total_tokens = 0, 0, 0
            if prompt_tokens < 0:
                prompt_tokens = 0
            if completion_tokens < 0:
                completion_tokens = 0
            if total_tokens < 0:
                total_tokens = 0
        except Exception as exc:
            outputs[agent.id] = {"_error": str(exc)}
            prompt_tokens, completion_tokens, total_tokens = 0, 0, 0
        duration_ms = (perf_counter() - agent_start) * 1000.0
        total_in += prompt_tokens
        total_out += completion_tokens
        per_agent[agent.id] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost_usd": None,
            "input_cost_usd": None,
            "output_cost_usd": None,
            "duration_ms": duration_ms,
            "model": payload.model,
        }
    try:
        prompt_price, completion_price = await get_model_pricing(payload.model)
    except Exception:
        prompt_price, completion_price = None, None
    total_cost: float | None = None
    if prompt_price is not None and completion_price is not None:
        total_cost = 0.0
        for entry in per_agent.values():
            cost = entry["prompt_tokens"] * prompt_price + entry["completion_tokens"] * completion_price
            entry["cost_usd"] = round(cost, 6)
            total_cost += cost
        total_cost = round(total_cost, 6)
    for entry in per_agent.values():
        if prompt_price is None:
            entry["input_cost_usd"] = None
        else:
            entry["input_cost_usd"] = round(entry["prompt_tokens"] * prompt_price, 6)
        if completion_price is None:
            entry["output_cost_usd"] = None
        else:
            entry["output_cost_usd"] = round(entry["completion_tokens"] * completion_price, 6)
    if prompt_price is None or any(v["input_cost_usd"] is None for v in per_agent.values()):
        total_input_cost: float | None = None
    else:
        total_input_cost = round(sum((v["input_cost_usd"] for v in per_agent.values()), 0.0), 6)
    if completion_price is None or any(v["output_cost_usd"] is None for v in per_agent.values()):
        total_output_cost: float | None = None
    else:
        total_output_cost = round(sum((v["output_cost_usd"] for v in per_agent.values()), 0.0), 6)
    wall_ms = (perf_counter() - wall_start) * 1000.0
    usage = {
        "prompt_tokens": total_in,
        "completion_tokens": total_out,
        "total_tokens": total_in + total_out,
        "cost_usd": total_cost,
        "input_cost_usd": total_input_cost,
        "output_cost_usd": total_output_cost,
        "duration_ms": wall_ms,
        "model": payload.model,
        "per_agent": per_agent,
    }
    run = Run(input_type=input_type, input_data=input_data, model=payload.model,
              agent_ids=payload.agent_ids, outputs=outputs)
    db.add(run)
    db.commit()
    db.refresh(run)
    # Denormalized log row — snapshots only, no FK to agents/attributes.
    db.add(RunLog(run_id=run.id, input_type=run.input_type, input_data=run.input_data, model=run.model,
                  agent_snapshot=snapshots, attribute_snapshot=snapshots, outputs=outputs, feedback={},
                  usage=usage, filters=filters))
    db.commit()
    return {"id": run.id, "outputs": outputs, "usage": usage}


@router.get("", response_model=list[RunOut])
def list_runs(db: Session = Depends(get_db)):
    rows = db.query(Run).order_by(Run.created_at.desc()).limit(100).all()
    return [RunOut(id=r.id, input_type=r.input_type, model=r.model,
                   agent_ids=r.agent_ids or [], created_at=r.created_at) for r in rows]


@router.get("/{run_id}", response_model=RunDetail)
def get_run(run_id: str, db: Session = Depends(get_db)):
    r = db.query(Run).filter(Run.id == run_id).first()
    if not r:
        raise HTTPException(404, "run not found")
    return RunDetail(id=r.id, input_type=r.input_type, model=r.model,
                     agent_ids=r.agent_ids or [], created_at=r.created_at,
                     input_data=r.input_data, outputs=r.outputs or {})


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
        # Re-assign so SQLAlchemy flags the JSON column dirty on every backend.
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(log, "feedback")
    db.commit()
    return {"ok": True}


@router.get("/{run_id}/feedback", response_model=list[FeedbackOut])
def list_feedback(run_id: str, db: Session = Depends(get_db)):
    if not db.query(Run).filter(Run.id == run_id).first():
        raise HTTPException(404, "run not found")
    rows = db.query(Feedback).filter(Feedback.run_id == run_id).order_by(Feedback.created_at.asc()).all()
    return [FeedbackOut(id=r.id, run_id=r.run_id, agent_name=r.agent_name,
                        attribute_name=r.attribute_name, rating=r.rating, remarks=r.remarks) for r in rows]
