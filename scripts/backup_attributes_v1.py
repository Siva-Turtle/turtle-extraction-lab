"""Backup v1 attribute catalogue (read-only, no writes, no migration).

Run from backend/ with:
    PYTHONPATH=. uv run python ../scripts/backup_attributes_v1.py

Dumps ALL rows of attributes and agent_attributes (plus agent id->name)
to exports/attributes_v1_backup_2026-10-03.json (indent 2, ISO datetimes).
This is the restore source for the 0017 downgrade data file.
Read-only queries only.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve()
for _cand in (_HERE.parents):
    if (_cand / "app" / "db" / "models.py").exists():
        if str(_cand) not in sys.path:
            sys.path.insert(0, str(_cand))
        break

REPO_ROOT = _HERE.parents[1] if len(_HERE.parents) >= 2 else Path(".")
# scripts/ is at repo root, backend/ is sibling
# REPO_ROOT = repo root containing exports/
if (REPO_ROOT / "exports").exists():
    pass
else:
    # fallback: cwd-based
    REPO_ROOT = Path(__file__).resolve().parents[1]

OUT_PATH = REPO_ROOT / "exports" / "attributes_v1_backup_2026-10-03.json"


def _iso(v):
    if isinstance(v, datetime):
        try:
            return v.isoformat()
        except Exception:
            return ""
    return v


def main() -> None:
    from app.db.models import Attribute, Agent, agent_attributes
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        attrs = db.query(Attribute).order_by(Attribute.group_name, Attribute.name).all()
        agents = db.query(Agent).all()
        agent_map = {a.id: a.name for a in agents}
        attr_rows = []
        for r in attrs:
            attr_rows.append(
                {
                    "id": r.id,
                    "name": r.name,
                    "type": r.type,
                    "description": r.description or "",
                    "enum_values": list(r.enum_values or []),
                    "object_properties": list(r.object_properties or []),
                    "group_name": getattr(r, "group_name", "") or "",
                    "array_items": dict(getattr(r, "array_items", None) or {}),
                    "created_at": _iso(getattr(r, "created_at", None)),
                }
            )
        links = []
        try:
            rows = db.execute(agent_attributes.select()).all()
            for row in rows:
                try:
                    links.append(
                        {"agent_id": row.agent_id, "attribute_id": row.attribute_id}
                    )
                except Exception:
                    # row as tuple
                    links.append({"agent_id": row[0], "attribute_id": row[1]})
        except Exception:
            links = []
        # deterministic order
        links.sort(key=lambda d: (d["agent_id"], d["attribute_id"]))
        payload = {
            "attributes": attr_rows,
            "agent_attributes": links,
            "agents": {k: v for k, v in sorted(agent_map.items())},
        }
    finally:
        db.close()

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"backup ok: attributes={len(payload['attributes'])} links={len(payload['agent_attributes'])} agents={len(payload['agents'])}")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
