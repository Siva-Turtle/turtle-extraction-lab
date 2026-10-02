from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.models import Agent, Attribute, agent_attributes
from app.db.session import get_db
from app.modules.attributes.schemas import (
    ATTRIBUTE_TYPES,
    OBJECT_SUB_TYPES,
    ArrayItems,
    AttributeCreate,
    AttributeOut,
    AttributeUpdate,
    ObjectProperty,
)

router = APIRouter(prefix="/api/v1/attributes", tags=["attributes"])

ARRAY_ITEM_KINDS = ("string", "number", "object")

DEFAULT_ARRAY_ITEMS = {"kind": "string", "properties": []}


def _agent_ids(db: Session, attribute_id: str) -> list[str]:
    rows = db.execute(
        agent_attributes.select().where(agent_attributes.c.attribute_id == attribute_id)
    ).all()
    return [r.agent_id for r in rows]


def _out(db: Session, r: Attribute) -> AttributeOut:
    raw_props = getattr(r, "object_properties", None) or []
    raw_group = getattr(r, "group_name", None) or ""
    raw_items = getattr(r, "array_items", None) or {}
    norm_items = _normalize_array_items(raw_items)
    return AttributeOut(id=r.id, agent_ids=_agent_ids(db, r.id), name=r.name, type=r.type,
                        description=r.description, group=raw_group,
                        enum_values=r.enum_values or [],
                        object_properties=raw_props if isinstance(raw_props, list) else [],
                        array_items=norm_items)


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


def _clean_group(raw: object) -> str:
    """Trimmed free text, default empty."""
    if raw is None:
        return ""
    return str(raw).strip()


def _clean_sub_enum(raw: object, ctx: str) -> list[str]:
    """Validate optional sub-field enum list (constrained string support)."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise HTTPException(422, f"{ctx} property enum must be a list of strings")
    cleaned: list[str] = []
    for v in raw:
        if not isinstance(v, str):
            raise HTTPException(422, f"{ctx} property enum must be a list of strings")
        t = v.strip()
        if t:
            cleaned.append(t)
    return cleaned


def _clean_sub_description(raw: object, ctx: str) -> str:
    """Validate optional sub-field description help-text."""
    if raw is None or raw == "":
        return ""
    if not isinstance(raw, str):
        raise HTTPException(422, f"{ctx} property description must be a string")
    return raw.strip()


def _as_array_items_dict(raw: object) -> dict:
    if isinstance(raw, ArrayItems):
        return {"kind": raw.kind, "properties": [p.model_dump() for p in raw.properties]}
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def _normalize_array_items(raw: object) -> dict:
    """Coerce stored array_items to {kind, properties} (defaults to string)."""
    d = _as_array_items_dict(raw)
    kind = str(d.get("kind", "string") or "string").strip().lower()
    if kind not in ARRAY_ITEM_KINDS:
        kind = "string"
    props_raw = d.get("properties", [])
    if kind != "object":
        return {"kind": kind, "properties": []}
    props = _as_prop_dicts(props_raw)
    cleaned: list[dict] = []
    for p in props:
        name = str(p.get("name", "") or "").strip()
        sub_type = p.get("type", "string")
        null_allowed = p.get("null_allowed", True)
        if name and sub_type in OBJECT_SUB_TYPES and isinstance(null_allowed, bool):
            entry: dict = {"name": name, "type": sub_type, "null_allowed": null_allowed}
            try:
                entry["enum"] = _clean_sub_enum(p.get("enum", []), "array")
                entry["description"] = _clean_sub_description(p.get("description", ""), "array")
            except HTTPException:
                entry["enum"] = []
                entry["description"] = ""
            cleaned.append(entry)
    return {"kind": kind, "properties": cleaned}


def _validate_array_items(raw: object) -> dict:
    d = _as_array_items_dict(raw)
    kind_raw = d.get("kind", "string")
    kind = str(kind_raw or "string").strip().lower()
    if kind not in ARRAY_ITEM_KINDS:
        raise HTTPException(422, "array kind must be string|number|object")
    if kind in ("string", "number"):
        return {"kind": kind, "properties": []}
    props = _as_prop_dicts(d.get("properties", []))
    if not props:
        raise HTTPException(422, "array object kind requires non-empty properties")
    cleaned: list[dict] = []
    seen: set[str] = set()
    for p in props:
        name = str(p.get("name", "") or "").strip()
        sub_type = p.get("type", "string")
        null_allowed = p.get("null_allowed", True)
        if not name:
            raise HTTPException(422, "array property name must be non-blank")
        if sub_type not in OBJECT_SUB_TYPES:
            raise HTTPException(422, "array property type must be string|number|boolean|array")
        if not isinstance(null_allowed, bool):
            raise HTTPException(422, "array property null_allowed must be boolean")
        if name in seen:
            raise HTTPException(422, f"duplicate array property: {name}")
        seen.add(name)
        cleaned.append({"name": name, "type": sub_type, "null_allowed": null_allowed,
                        "enum": _clean_sub_enum(p.get("enum", []), "array"),
                        "description": _clean_sub_description(p.get("description", ""), "array")})
    return {"kind": kind, "properties": cleaned}


def _validate(attr_type: str, enum_values: list[str],
              object_properties: object = None,
              array_items: object = None) -> tuple[list[str], list[dict], dict]:
    if attr_type not in ATTRIBUTE_TYPES:
        raise HTTPException(422, "type must be string|number|boolean|enum|array|object")
    if attr_type == "enum":
        if not enum_values:
            raise HTTPException(422, "enum requires non-empty enum_values")
        return list(enum_values), [], dict(DEFAULT_ARRAY_ITEMS)
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
            cleaned.append({"name": name, "type": sub_type, "null_allowed": null_allowed,
                            "enum": _clean_sub_enum(p.get("enum", []), "object"),
                            "description": _clean_sub_description(p.get("description", ""), "object")})
        return [], cleaned, dict(DEFAULT_ARRAY_ITEMS)
    if attr_type == "array":
        return [], [], _validate_array_items(array_items if array_items is not None else {})
    # string | number | boolean store no extra config.
    return [], [], dict(DEFAULT_ARRAY_ITEMS)


@router.get("", response_model=list[AttributeOut])
def list_attributes(agent_id: str | None = None, group: str | None = None,
                    db: Session = Depends(get_db)):
    q = db.query(Attribute).order_by(Attribute.created_at.desc())
    if agent_id:
        q = q.join(agent_attributes,
                   agent_attributes.c.attribute_id == Attribute.id
                   ).filter(agent_attributes.c.agent_id == agent_id)
    if group is not None and str(group).strip() != "":
        q = q.filter(Attribute.group_name == str(group).strip())
    return [_out(db, r) for r in q.limit(500).all()]


@router.post("", response_model=AttributeOut)
def create_attribute(payload: AttributeCreate, db: Session = Depends(get_db)):
    _check_agents_exist(db, payload.agent_ids)
    enum_values, object_properties, array_items = _validate(
        payload.type, payload.enum_values or [], payload.object_properties or [],
        payload.array_items if payload.array_items is not None else {})
    row = Attribute(name=payload.name, type=payload.type,
                    description=payload.description,
                    group_name=_clean_group(getattr(payload, "group", "")),
                    enum_values=enum_values,
                    object_properties=object_properties,
                    array_items=array_items)
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
    if (payload.enum_values is not None or payload.object_properties is not None
            or payload.array_items is not None or payload.type is not None):
        new_enum = payload.enum_values if payload.enum_values is not None else (row.enum_values or [])
        new_props_raw = (payload.object_properties if payload.object_properties is not None
                         else (getattr(row, "object_properties", None) or []))
        new_items_raw = (payload.array_items if payload.array_items is not None
                         else (getattr(row, "array_items", None) or {}))
        row.enum_values, row.object_properties, row.array_items = _validate(
            new_type, new_enum, new_props_raw, new_items_raw)
    if payload.group is not None:
        row.group_name = _clean_group(payload.group)
    for field in ("name", "type", "description"):
        value = getattr(payload, field)
        if value is not None:
            setattr(row, field, value)
    # Non-enum types never store options; non-object types never store sub-fields;
    # non-array types reset item-shape to the string default (ignored).
    if row.type != "enum":
        row.enum_values = []
    if row.type != "object":
        row.object_properties = []
    if row.type != "array":
        row.array_items = dict(DEFAULT_ARRAY_ITEMS)
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
