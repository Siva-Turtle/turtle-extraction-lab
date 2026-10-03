"""Attributes v2 (rev 2): 7 list attributes, no outer wrapper.

Revision ID: 0017_attributes_v2
Revises: 0016_split_agents
Create Date: 2026-10-03

- Adds attributes.wrap_result Boolean NOT NULL server_default true.
- Deletes every attribute whose group_name (strip, case-insensitive) is one
  of Assets, Banking / Accounts, Expenses, Goals, Income, Liabilities,
  Insurance - EXCEPT term_insurance_coverage_adequacy and
  health_insurance_coverage_adequacy. Agent links deleted explicitly first.
- Inserts the 7 new list attributes (type array, wrap_result False) from
  0017_attributes_v2_data.json (frozen, loaded relative to __file__).
  Links by agent name; missing agents skipped silently. Idempotent: skips
  names that already exist.
- Downgrade deletes the 7 + links, restores deleted v1 rows from
  0017_attributes_v1_deleted.json (same ids, all columns, links by agent
  name), then drops wrap_result.

Data files must not import scripts/ (frozen JSON next to this file).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0017_attributes_v2"
down_revision = "0016_split_agents"
branch_labels = None
depends_on = None

DELETE_GROUPS = {
    "assets",
    "banking / accounts",
    "expenses",
    "goals",
    "income",
    "liabilities",
    "insurance",
}

KEEP_NAMES = {
    "term_insurance_coverage_adequacy",
    "health_insurance_coverage_adequacy",
}

V2_NAMES = {
    "assets",
    "accounts",
    "expenses",
    "goals",
    "income_sources",
    "insurance_policies",
    "liabilities",
}


def _data_path(name: str) -> Path:
    return Path(__file__).resolve().parent / name


def _load_json(name: str):
    p = _data_path(name)
    return json.loads(p.read_text(encoding="utf-8"))


def _now():
    return datetime.now(timezone.utc)


attributes_tbl = sa.table(
    "attributes",
    sa.column("id", sa.String(36)),
    sa.column("name", sa.String(200)),
    sa.column("type", sa.String(64)),
    sa.column("description", sa.Text()),
    sa.column("enum_values", sa.JSON()),
    sa.column("object_properties", sa.JSON()),
    sa.column("group_name", sa.String(200)),
    sa.column("array_items", sa.JSON()),
    sa.column("wrap_result", sa.Boolean()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)

agents_tbl = sa.table(
    "agents",
    sa.column("id", sa.String(36)),
    sa.column("name", sa.String(200)),
)

agent_attributes_tbl = sa.table(
    "agent_attributes",
    sa.column("agent_id", sa.String(36)),
    sa.column("attribute_id", sa.String(36)),
)


def _parse_created_at(raw):
    if isinstance(raw, str) and raw.strip():
        try:
            return datetime.fromisoformat(raw)
        except Exception:
            return _now()
    return _now()


def upgrade() -> None:
    # a) new flag
    try:
        op.add_column(
            "attributes",
            sa.Column("wrap_result", sa.Boolean(), server_default=sa.true(), nullable=False),
        )
    except Exception:
        # Column already exists (re-run): continue idempotently.
        pass
    conn = op.get_bind()
    # Backfill any NULLs to True (fresh server_default covers new rows).
    try:
        conn.execute(
            attributes_tbl.update()
            .where(attributes_tbl.c.wrap_result.is_(None))
            .values(wrap_result=True)
        )
    except Exception:
        pass

    # b) delete v1 attributes in the 7 replaced groups (except adequacy)
    try:
        rows = conn.execute(
            sa.select(attributes_tbl.c.id, attributes_tbl.c.name, attributes_tbl.c.group_name)
        ).fetchall()
    except Exception:
        rows = []
    to_delete: list[str] = []
    for attr_id, name, group_name in rows:
        try:
            g = str(group_name or "").strip().lower()
        except Exception:
            continue
        if g in DELETE_GROUPS and str(name or "") not in KEEP_NAMES:
            to_delete.append(attr_id)
    if to_delete:
        conn.execute(
            agent_attributes_tbl.delete().where(
                agent_attributes_tbl.c.attribute_id.in_(to_delete)
            )
        )
        conn.execute(attributes_tbl.delete().where(attributes_tbl.c.id.in_(to_delete)))

    # c) insert the 7 new attributes
    try:
        v2_data = _load_json("0017_attributes_v2_data.json")
    except Exception:
        v2_data = []
    try:
        existing_names = {
            r[0]
            for r in conn.execute(sa.select(attributes_tbl.c.name)).fetchall()
        }
    except Exception:
        existing_names = set()
    try:
        agents = {r[1]: r[0] for r in conn.execute(sa.select(agents_tbl.c.id, agents_tbl.c.name)).fetchall()}
    except Exception:
        agents = {}
    for item in v2_data or []:
        name = str(item.get("name") or "")
        if not name or name in existing_names:
            continue
        attr_id = str(item.get("id") or uuid.uuid4().hex)
        conn.execute(
            attributes_tbl.insert().values(
                id=attr_id,
                name=name,
                type=str(item.get("type") or "array"),
                description=str(item.get("description") or ""),
                enum_values=list(item.get("enum_values") or []),
                object_properties=list(item.get("object_properties") or []),
                group_name=str(item.get("group_name") or ""),
                array_items=dict(item.get("array_items") or {"kind": "string", "properties": []}),
                wrap_result=bool(item.get("wrap_result", False)) if "wrap_result" in item else False,
                created_at=_now(),
            )
        )
        existing_names.add(name)
        for agent_name in (item.get("agents") or []):
            agent_id = agents.get(agent_name)
            if not agent_id:
                continue
            try:
                already = conn.execute(
                    sa.select(agent_attributes_tbl.c.agent_id).where(
                        (agent_attributes_tbl.c.agent_id == agent_id)
                        & (agent_attributes_tbl.c.attribute_id == attr_id)
                    )
                ).fetchone()
            except Exception:
                already = None
            if already is None:
                conn.execute(
                    agent_attributes_tbl.insert().values(agent_id=agent_id, attribute_id=attr_id)
                )


def downgrade() -> None:
    conn = op.get_bind()
    # Delete the 7 new attributes (+links)
    try:
        v2_ids = [
            r[0]
            for r in conn.execute(
                sa.select(attributes_tbl.c.id).where(attributes_tbl.c.name.in_(list(V2_NAMES)))
            ).fetchall()
        ]
    except Exception:
        v2_ids = []
    if v2_ids:
        conn.execute(
            agent_attributes_tbl.delete().where(
                agent_attributes_tbl.c.attribute_id.in_(v2_ids)
            )
        )
        conn.execute(attributes_tbl.delete().where(attributes_tbl.c.id.in_(v2_ids)))

    # Restore deleted v1 rows (same ids, all columns, links by agent name)
    try:
        v1_data = _load_json("0017_attributes_v1_deleted.json")
    except Exception:
        v1_data = []
    try:
        agents = {r[1]: r[0] for r in conn.execute(sa.select(agents_tbl.c.id, agents_tbl.c.name)).fetchall()}
    except Exception:
        agents = {}
    for item in v1_data or []:
        attr_id = str(item.get("id") or "")
        name = str(item.get("name") or "")
        if not attr_id or not name:
            continue
        try:
            exists = conn.execute(
                sa.select(attributes_tbl.c.id).where(attributes_tbl.c.id == attr_id)
            ).fetchone()
        except Exception:
            exists = None
        if exists is None:
            try:
                exists_by_name = conn.execute(
                    sa.select(attributes_tbl.c.id).where(attributes_tbl.c.name == name)
                ).fetchone()
            except Exception:
                exists_by_name = None
            if exists_by_name is not None:
                continue
            conn.execute(
                attributes_tbl.insert().values(
                    id=attr_id,
                    name=name,
                    type=str(item.get("type") or "string"),
                    description=str(item.get("description") or ""),
                    enum_values=list(item.get("enum_values") or []),
                    object_properties=list(item.get("object_properties") or []),
                    group_name=str(item.get("group_name") or ""),
                    array_items=dict(item.get("array_items") or {"kind": "string", "properties": []}),
                    wrap_result=True,
                    created_at=_parse_created_at(item.get("created_at")),
                )
            )
        for agent_name in (item.get("agents") or []):
            agent_id = agents.get(agent_name)
            if not agent_id:
                continue
            try:
                already = conn.execute(
                    sa.select(agent_attributes_tbl.c.agent_id).where(
                        (agent_attributes_tbl.c.agent_id == agent_id)
                        & (agent_attributes_tbl.c.attribute_id == attr_id)
                    )
                ).fetchone()
            except Exception:
                already = None
            if already is None:
                conn.execute(
                    agent_attributes_tbl.insert().values(agent_id=agent_id, attribute_id=attr_id)
                )

    # Drop the flag last
    try:
        op.drop_column("attributes", "wrap_result")
    except Exception:
        pass
