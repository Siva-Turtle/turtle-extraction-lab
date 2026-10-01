from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Agent, Attribute, agent_attributes
from app.db.session import get_db
from app.modules.agents.schemas import AgentCreate, AgentOut, AgentUpdate
from app.modules.runs.router import (
    _agent_system_content,
    _attrs_for_agent,
    build_extraction_schema,
)

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])

# Shown in place of the transcript: the real user content is pulled
# automatically at run time, so a preview costs zero tokens.
USER_TEMPLATE = "<transcription — pulled automatically on run>"


def _out(r: Agent) -> AgentOut:
    return AgentOut(id=r.id, name=r.name, system_instruction=r.system_instruction,
                    input_types=r.input_types or [],
                    is_enabled=r.is_enabled if r.is_enabled is not None else True)


@router.get("", response_model=list[AgentOut])
def list_agents(db: Session = Depends(get_db)):
    rows = db.query(Agent).order_by(Agent.created_at.desc()).limit(200).all()
    return [_out(r) for r in rows]


@router.post("", response_model=AgentOut)
def create_agent(payload: AgentCreate, db: Session = Depends(get_db)):
    row = Agent(name=payload.name, system_instruction=payload.system_instruction,
                input_types=payload.input_types,
                is_enabled=payload.is_enabled)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _out(row)


@router.get("/{agent_id}", response_model=AgentOut)
def get_agent(agent_id: str, db: Session = Depends(get_db)):
    row = db.query(Agent).filter(Agent.id == agent_id).first()
    if not row:
        raise HTTPException(404, "agent not found")
    return _out(row)


@router.patch("/{agent_id}", response_model=AgentOut)
def update_agent(agent_id: str, payload: AgentUpdate, db: Session = Depends(get_db)):
    row = db.query(Agent).filter(Agent.id == agent_id).first()
    if not row:
        raise HTTPException(404, "agent not found")
    for field in ("name", "system_instruction", "input_types", "is_enabled"):
        value = getattr(payload, field)
        if value is not None:
            setattr(row, field, value)
    if payload.attribute_ids is not None:
        # Replace semantics: validate every id, then swap the link set.
        ids = list(dict.fromkeys(payload.attribute_ids))
        if ids:
            found = {r.id for r in db.query(Attribute).filter(Attribute.id.in_(ids)).all()}
            if len(found) != len(ids):
                raise HTTPException(404, "attribute not found")
        db.execute(agent_attributes.delete().where(
            agent_attributes.c.agent_id == agent_id))
        for attribute_id in ids:
            db.execute(agent_attributes.insert().values(
                agent_id=agent_id, attribute_id=attribute_id))
    db.commit()
    db.refresh(row)
    return _out(row)


@router.get("/{agent_id}/prompt-preview")
def prompt_preview(agent_id: str, db: Session = Depends(get_db)):
    """Dry-run preview: the exact system prompt + response_format a run
    would send for this agent, with a placeholder where the transcript
    goes. Built with the runs builders (single source of truth)."""
    row = db.query(Agent).filter(Agent.id == agent_id).first()
    if not row:
        raise HTTPException(404, "agent not found")
    attrs = _attrs_for_agent(db, agent_id)
    system = _agent_system_content(row, attrs)
    schema = build_extraction_schema(attrs)
    if schema is None:
        response_format: dict = {"type": "json_object"}
    else:
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": "meeting_extraction", "strict": True, "schema": schema},
        }
    return {
        "agent_id": row.id,
        "system": system,
        "user_template": USER_TEMPLATE,
        "response_format": response_format,
        "attributes": [{"name": a.name, "type": a.type, "description": a.description,
                        "enum_values": a.enum_values or []} for a in attrs],
    }


@router.delete("/{agent_id}")
def delete_agent(agent_id: str, db: Session = Depends(get_db)):
    row = db.query(Agent).filter(Agent.id == agent_id).first()
    if not row:
        raise HTTPException(404, "agent not found")
    # Drop this agent's association links, then delete attributes left with
    # ZERO links (orphans). Shared attributes (still linked elsewhere) survive.
    db.execute(agent_attributes.delete().where(agent_attributes.c.agent_id == agent_id))
    still_linked = select(agent_attributes.c.attribute_id)
    db.query(Attribute).filter(~Attribute.id.in_(still_linked)).delete(synchronize_session=False)
    db.delete(row)
    db.commit()
    return {"ok": True}
