from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.models import Agent, Attribute
from app.db.session import get_db
from app.modules.agents.schemas import AgentCreate, AgentOut, AgentUpdate

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


def _out(r: Agent) -> AgentOut:
    return AgentOut(id=r.id, name=r.name, system_instruction=r.system_instruction,
                    prompt=r.prompt, input_types=r.input_types or [],
                    is_enabled=r.is_enabled if r.is_enabled is not None else True)


@router.get("", response_model=list[AgentOut])
def list_agents(db: Session = Depends(get_db)):
    rows = db.query(Agent).order_by(Agent.created_at.desc()).limit(200).all()
    return [_out(r) for r in rows]


@router.post("", response_model=AgentOut)
def create_agent(payload: AgentCreate, db: Session = Depends(get_db)):
    row = Agent(name=payload.name, system_instruction=payload.system_instruction,
                prompt=payload.prompt, input_types=payload.input_types,
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
    for field in ("name", "system_instruction", "prompt", "input_types", "is_enabled"):
        value = getattr(payload, field)
        if value is not None:
            setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return _out(row)


@router.delete("/{agent_id}")
def delete_agent(agent_id: str, db: Session = Depends(get_db)):
    row = db.query(Agent).filter(Agent.id == agent_id).first()
    if not row:
        raise HTTPException(404, "agent not found")
    # Lab convenience: an agent's attributes go with it.
    db.query(Attribute).filter(Attribute.agent_id == agent_id).delete()
    db.delete(row)
    db.commit()
    return {"ok": True}
