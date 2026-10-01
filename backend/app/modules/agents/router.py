from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.models import Agent
from app.db.session import get_db
from app.modules.agents.schemas import AgentCreate, AgentOut

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


@router.get("", response_model=list[AgentOut])
def list_agents(db: Session = Depends(get_db)):
    rows = db.query(Agent).order_by(Agent.created_at.desc()).limit(200).all()
    return [AgentOut(id=r.id, name=r.name, system_instruction=r.system_instruction, prompt=r.prompt, input_types=r.input_types or []) for r in rows]


@router.post("", response_model=AgentOut)
def create_agent(payload: AgentCreate, db: Session = Depends(get_db)):
    row = Agent(name=payload.name, system_instruction=payload.system_instruction, prompt=payload.prompt, input_types=payload.input_types)
    db.add(row)
    db.commit()
    db.refresh(row)
    return AgentOut(id=row.id, name=row.name, system_instruction=row.system_instruction, prompt=row.prompt, input_types=row.input_types or [])
