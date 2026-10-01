from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.models import Agent, Attribute
from app.db.session import get_db
from app.modules.attributes.schemas import AttributeCreate, AttributeOut, AttributeUpdate

router = APIRouter(prefix="/api/v1/attributes", tags=["attributes"])


def _out(r: Attribute) -> AttributeOut:
    return AttributeOut(id=r.id, agent_id=r.agent_id, name=r.name, type=r.type,
                        description=r.description, json_schema=r.json_schema or {},
                        required=bool(r.required))


@router.get("", response_model=list[AttributeOut])
def list_attributes(agent_id: str | None = None, db: Session = Depends(get_db)):
    q = db.query(Attribute).order_by(Attribute.created_at.desc())
    if agent_id:
        q = q.filter(Attribute.agent_id == agent_id)
    return [_out(r) for r in q.limit(500).all()]


@router.post("", response_model=AttributeOut)
def create_attribute(payload: AttributeCreate, db: Session = Depends(get_db)):
    if not db.query(Agent).filter(Agent.id == payload.agent_id).first():
        raise HTTPException(404, "agent not found")
    row = Attribute(agent_id=payload.agent_id, name=payload.name, type=payload.type,
                    description=payload.description, json_schema=payload.json_schema,
                    required=payload.required)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _out(row)


@router.get("/{attribute_id}", response_model=AttributeOut)
def get_attribute(attribute_id: str, db: Session = Depends(get_db)):
    row = db.query(Attribute).filter(Attribute.id == attribute_id).first()
    if not row:
        raise HTTPException(404, "attribute not found")
    return _out(row)


@router.patch("/{attribute_id}", response_model=AttributeOut)
def update_attribute(attribute_id: str, payload: AttributeUpdate, db: Session = Depends(get_db)):
    row = db.query(Attribute).filter(Attribute.id == attribute_id).first()
    if not row:
        raise HTTPException(404, "attribute not found")
    if payload.agent_id is not None:
        if not db.query(Agent).filter(Agent.id == payload.agent_id).first():
            raise HTTPException(404, "agent not found")
        row.agent_id = payload.agent_id
    for field in ("name", "type", "description", "json_schema", "required"):
        value = getattr(payload, field)
        if value is not None:
            setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return _out(row)


@router.delete("/{attribute_id}")
def delete_attribute(attribute_id: str, db: Session = Depends(get_db)):
    row = db.query(Attribute).filter(Attribute.id == attribute_id).first()
    if not row:
        raise HTTPException(404, "attribute not found")
    db.delete(row)
    db.commit()
    return {"ok": True}
