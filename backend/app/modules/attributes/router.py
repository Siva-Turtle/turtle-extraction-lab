from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.models import Attribute
from app.db.session import get_db
from app.modules.attributes.schemas import AttributeCreate, AttributeOut

router = APIRouter(prefix="/api/v1/attributes", tags=["attributes"])


@router.get("", response_model=list[AttributeOut])
def list_attributes(agent_id: str | None = None, db: Session = Depends(get_db)):
    q = db.query(Attribute).order_by(Attribute.created_at.desc()).limit(500)
    if agent_id:
        q = db.query(Attribute).filter(Attribute.agent_id == agent_id).order_by(Attribute.created_at.desc()).limit(500)
    rows = q.all()
    return [AttributeOut(id=r.id, agent_id=r.agent_id, name=r.name, type=r.type, description=r.description, json_schema=r.json_schema or {}, required=bool(r.required)) for r in rows]


@router.post("", response_model=AttributeOut)
def create_attribute(payload: AttributeCreate, db: Session = Depends(get_db)):
    row = Attribute(agent_id=payload.agent_id, name=payload.name, type=payload.type, description=payload.description, json_schema=payload.json_schema, required=payload.required)
    db.add(row)
    db.commit()
    db.refresh(row)
    return AttributeOut(id=row.id, agent_id=row.agent_id, name=row.name, type=row.type, description=row.description, json_schema=row.json_schema or {}, required=bool(row.required))
