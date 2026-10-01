from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.models import Agent, Attribute, agent_attributes
from app.db.session import get_db
from app.modules.attributes.schemas import (
    ATTRIBUTE_TYPES,
    OBJECT_SUB_TYPES,
    AttributeCreate,
    AttributeOut,
    AttributeUpdate,
    ObjectProperty,
)

router = APIRouter(prefix="/api/v1/attributes", tags=["attributes"])


def _agent_ids(db: Session, attribute_id: str) -> list[str]:
    rows = db.execute(
        agent_attributes.select().where(agent_attributes.c.attribute_id == attribute_id)
    ).all()
    return [r.agent_id for r in rows]


def _out(db: Session, r: Attribute) -> AttributeOut:
    raw_props = getattr(r, "object_properties", None) or []
    return AttributeOut(id=r.id, agent_ids=_agent_ids(db, r.id), name=r.name, type=r.type,
                        description=r.description, enum_values=r.enum_values or [],
                        object_properties=raw_props if isinstance(raw_props, list) else [])


def _check_agents_exist(db: Session, agent_ids: list[str]) -> None:
    found = {r.id for r in db.query(Agent).filter(Agent.id.in_(agent_ids)).all()}
    if len(found) != len(set(agent_ids)):
        raise HTTPException(404, "agent not found")


def _as_prop_dicts(raw: object) -> list[dict]:
    """Normalize object_properties input (pydantic models or plain dicts)."""
    props: list[dict] = []
    for p in raw or []:  # type: ignore[union-attr]
        if isinstance(p, ObjectProperty):
            props.append(p.model_dump())
        elif isinstance(p, dict):
            props.append(dict(p))
    return props


def _validate(attr_type: str, enum_values: list[str],
              object_properties: object = None) -> tuple[list[str], list[dict]]:
    if attr_type not in ATTRIBUTE_TYPES:
        raise HTTPException(422, "type must be string|number|boolean|enum|array|object")
    if attr_type == "enum":
        if not enum_values:
            raise HTTPException(422, "enum requires non-empty enum_values")
        return list(enum_values), []
    if attr_type == "object":
        props = _as_prop_dicts(object_properties)
        if not props:
            raise HTTPException(422, "object requires non-empty object_properties")
        cleaned: list[dict] = []
        seen: set[str] = set()
        for p in props:
            name = str(p.get("name", "") or "").strip()
            sub_type = p.get("type", "string")
            null_allowed = p.get("null_allowed", True)
            if not name:
                raise HTTPException(422, "object property name must be non-blank")
            if sub_type not in OBJECT_SUB_TYPES:
                raise HTTPException(422, "object property type must be string|number|boolean|array")
            if not isinstance(null_allowed, bool):
                raise HTTPException(422, "object property null_allowed must be boolean")
            if name in seen:
                raise HTTPException(422, f"duplicate object property: {name}")
            seen.add(name)
            cleaned.append({"name": name, "type": sub_type, "null_allowed": null_allowed})
        return [], cleaned
    # string | number | boolean | array store no extra config.
    return [], []


@router.get("", response_model=list[AttributeOut])
def list_attributes(agent_id: str | None = None, db: Session = Depends(get_db)):
    q = db.query(Attribute).order_by(Attribute.created_at.desc())
    if agent_id:
        q = q.join(agent_attributes,
                   agent_attributes.c.attribute_id == Attribute.id
                   ).filter(agent_attributes.c.agent_id == agent_id)
    return [_out(db, r) for r in q.limit(500).all()]


@router.post("", response_model=AttributeOut)
def create_attribute(payload: AttributeCreate, db: Session = Depends(get_db)):
    _check_agents_exist(db, payload.agent_ids)
    enum_values, object_properties = _validate(
        payload.type, payload.enum_values or [], payload.object_properties or [])
    row = Attribute(name=payload.name, type=payload.type,
                    description=payload.description, enum_values=enum_values,
                    object_properties=object_properties)
    db.add(row)
    db.flush()  # need row.id for the association links
    for aid in dict.fromkeys(payload.agent_ids):
        db.execute(agent_attributes.insert().values(agent_id=aid, attribute_id=row.id))
    db.commit()
    db.refresh(row)
    return _out(db, row)


@router.get("/{attribute_id}", response_model=AttributeOut)
def get_attribute(attribute_id: str, db: Session = Depends(get_db)):
    row = db.query(Attribute).filter(Attribute.id == attribute_id).first()
    if not row:
        raise HTTPException(404, "attribute not found")
    return _out(db, row)


@router.patch("/{attribute_id}", response_model=AttributeOut)
def update_attribute(attribute_id: str, payload: AttributeUpdate, db: Session = Depends(get_db)):
    row = db.query(Attribute).filter(Attribute.id == attribute_id).first()
    if not row:
        raise HTTPException(404, "attribute not found")
    if payload.agent_ids is not None:
        if not payload.agent_ids:
            raise HTTPException(422, "agent_ids must not be empty")
        _check_agents_exist(db, payload.agent_ids)
        db.execute(agent_attributes.delete().where(
            agent_attributes.c.attribute_id == attribute_id))
        for aid in dict.fromkeys(payload.agent_ids):
            db.execute(agent_attributes.insert().values(agent_id=aid, attribute_id=row.id))
    new_type = payload.type if payload.type is not None else row.type
    if payload.type is not None and payload.type not in ATTRIBUTE_TYPES:
        raise HTTPException(422, "type must be string|number|boolean|enum|array|object")
    if payload.enum_values is not None or payload.object_properties is not None or payload.type is not None:
        new_enum = payload.enum_values if payload.enum_values is not None else (row.enum_values or [])
        new_props_raw = (payload.object_properties if payload.object_properties is not None
                         else (getattr(row, "object_properties", None) or []))
        row.enum_values, row.object_properties = _validate(new_type, new_enum, new_props_raw)
    for field in ("name", "type", "description"):
        value = getattr(payload, field)
        if value is not None:
            setattr(row, field, value)
    # Non-enum types never store options; non-object types never store sub-fields.
    if row.type != "enum":
        row.enum_values = []
    if row.type != "object":
        row.object_properties = []
    db.commit()
    db.refresh(row)
    return _out(db, row)


@router.delete("/{attribute_id}")
def delete_attribute(attribute_id: str, db: Session = Depends(get_db)):
    row = db.query(Attribute).filter(Attribute.id == attribute_id).first()
    if not row:
        raise HTTPException(404, "attribute not found")
    db.execute(agent_attributes.delete().where(
        agent_attributes.c.attribute_id == attribute_id))
    db.delete(row)
    db.commit()
    return {"ok": True}
