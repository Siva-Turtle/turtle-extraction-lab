"""Upsert meeting-transcript extraction CSV rows as attributes.

Usage:
    python scripts/import_attributes_csv.py <csv-path> [--agent-name NAME] [--database-url URL]

Upsert only (never wipes): matches by normalized snake_case `name`; existing
rows are updated in place, new rows are created. When --agent-name is given,
all imported attributes are linked to that agent (created if missing) without
removing any existing links.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

# Allow `python backend/scripts/...` from repo root or backend/.
_HERE = Path(__file__).resolve()
for _cand in (_HERE.parents):
    if (_cand / "app" / "db" / "models.py").exists():
        if str(_cand) not in sys.path:
            sys.path.insert(0, str(_cand))
        break

from app.db.models import Agent, Attribute, agent_attributes  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.db.session import Base  # noqa: E402
from app.modules.attributes.csv_import import (  # noqa: E402
    parse_row,
    unique_name,
)


def _parse_wrap_result(raw: object) -> bool:
    """wrap_result CSV column: missing/blank = True (backward compatible)."""
    if raw is None:
        return True
    s = str(raw).strip().lower()
    if s == "":
        return True
    if s in ("0", "false", "no", "n", "f"):
        return False
    if s in ("1", "true", "yes", "y", "t"):
        return True
    return True


def import_csv(csv_path: str, db, agent_name: str | None = None) -> dict:
    existing: dict[str, object] = {a.name: a for a in db.query(Attribute).all()}
    taken: set[str] = set(existing.keys())
    # Re-run idempotency matches by attribute NAME. Old "[Original: X]"
    # markers are still *read* for backward compatibility (pre-0018 rows),
    # but never written (csv_import.build_description no longer emits them).
    marker_to_name: dict[str, str] = {}
    for nm, row_obj in existing.items():
        desc = getattr(row_obj, "description", "") or ""
        idx = desc.rfind("[Original: ")
        if idx != -1 and desc.endswith("]"):
            orig = desc[idx + len("[Original: "):-1].strip()
            if orig:
                marker_to_name.setdefault(orig, nm)
    claimed: dict[str, str] = {}  # base -> assigned name this run
    agent = None
    if agent_name:
        agent = db.query(Agent).filter(Agent.name == agent_name).first()
        if agent is None:
            agent = Agent(name=agent_name, system_instruction="",
                          input_types=["transcription"])
            db.add(agent)
            db.flush()
    created = 0
    updated = 0
    collisions: list[tuple[str, str]] = []
    total = 0
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Header: Group,Property,Type,Description,enum values or object json
            group = row.get("Group", "") or ""
            prop = row.get("Property", "") or ""
            typ = row.get("Type", "") or ""
            desc = row.get("Description", "") or ""
            enum_raw = (row.get("enum values or object json", "")
                        or row.get("enum values or object json ", "") or "")
            if not prop.strip():
                continue
            total += 1
            original = prop.strip()
            parsed = parse_row(group, prop, typ, desc, enum_raw)
            # Optional wrap_result column (missing = True for backward compat).
            wrap_raw = None
            try:
                for _k in ("wrap_result", "Wrap result", "wrap result", "Wrap_result"):
                    if _k in (row or {}):
                        wrap_raw = row.get(_k)
                        break
            except Exception:
                wrap_raw = None
            parsed["wrap_result"] = _parse_wrap_result(wrap_raw)
            base = parsed["name"]
            if base in existing:
                # Primary: match by attribute name (post-0018, no markers).
                name = base
            elif original in marker_to_name:
                # Backward compat: pre-0018 row still carrying its marker.
                name = marker_to_name[original]
            elif base not in taken:
                name = base
            else:
                # Base taken by the DB (seed/unrelated) or an earlier row
                # with a different original -> suffix with the group slug.
                name = unique_name(base, parsed["group"], taken)
                if name != base:
                    collisions.append((original, name))
            taken.add(name)
            marker_to_name.setdefault(original, name)
            row_obj = existing.get(name)
            if row_obj is None:
                row_obj = Attribute(
                    name=name, type=parsed["type"],
                    description=parsed["description"],
                    group_name=parsed["group"],
                    enum_values=parsed["enum_values"],
                    object_properties=parsed["object_properties"],
                    array_items=parsed["array_items"],
                    wrap_result=parsed.get("wrap_result", True),
                )
                db.add(row_obj)
                db.flush()
                existing[name] = row_obj
                created += 1
            else:
                row_obj.type = parsed["type"]
                row_obj.description = parsed["description"]
                setattr(row_obj, "group_name", parsed["group"])
                row_obj.enum_values = parsed["enum_values"]
                row_obj.object_properties = parsed["object_properties"]
                row_obj.array_items = parsed["array_items"]
                try:
                    row_obj.wrap_result = parsed.get("wrap_result", True)
                except Exception:
                    pass
                updated += 1
            if agent is not None:
                link = db.execute(
                    agent_attributes.select().where(
                        agent_attributes.c.agent_id == agent.id,
                        agent_attributes.c.attribute_id == row_obj.id)
                ).first()
                if link is None:
                    db.execute(agent_attributes.insert().values(
                        agent_id=agent.id, attribute_id=row_obj.id))
    db.commit()
    return {"total": total, "created": created, "updated": updated,
            "collisions": collisions}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path")
    ap.add_argument("--agent-name", default=None)
    ap.add_argument("--database-url", default=None)
    args = ap.parse_args()
    if args.database_url:
        # Scratch DB run: create tables on the given URL.
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        eng = create_engine(args.database_url)
        Base.metadata.create_all(bind=eng)
        Sess = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
        db = Sess()
    else:
        db = SessionLocal()
    try:
        stats = import_csv(args.csv_path, db, agent_name=args.agent_name)
    finally:
        db.close()
    print(f"import ok: total={stats['total']} created={stats['created']} "
          f"updated={stats['updated']} collisions={len(stats['collisions'])}")
    for orig, name in stats["collisions"]:
        print(f"  collision: {orig!r} -> {name!r}")


if __name__ == "__main__":
    main()
