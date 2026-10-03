"""Split combined extraction agents into single-purpose agents (data-only).

Revision ID: 0016_split_agents
Revises: 0015_runlog_provider
Create Date: 2026-10-03

Creates 4 extraction agents next to the 2 combined ones:
- kc: Karma Conversation group only
- feedback: Feedback + Sentiment groups
- tax: Tax + Tax / Compliance groups
- insurance: Insurance group

Each copies input_types / system_instruction / kind / prompt from its
combined source agent; links are added by group (strip +
case-insensitive). Idempotent: agents are created only when no agent
with that name exists; links use an existence check. Downgrade removes
the 4 agents' agent_attributes rows and the agents.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0016_split_agents"
down_revision = "0015_runlog_provider"
branch_labels = None
depends_on = None


agents_tbl = sa.table(
    "agents",
    sa.column("id", sa.String(36)),
    sa.column("name", sa.String(200)),
    sa.column("description", sa.Text()),
    sa.column("kind", sa.String(32)),
    sa.column("system_instruction", sa.Text()),
    sa.column("prompt", sa.Text()),
    sa.column("input_types", sa.JSON()),
    sa.column("is_enabled", sa.Boolean()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)

attributes_tbl = sa.table(
    "attributes",
    sa.column("id", sa.String(36)),
    sa.column("group_name", sa.String(200)),
)

agent_attributes_tbl = sa.table(
    "agent_attributes",
    sa.column("agent_id", sa.String(36)),
    sa.column("attribute_id", sa.String(36)),
)

# (new name, description, source combined name, groups lower-stripped).
NEW_AGENTS: list[tuple[str, str, str, list[str]]] = [
    ("kc", "Karma Conversation attributes only",
     "kc_and_feedback", ["karma conversation"]),
    ("feedback", "Feedback and sentiment attributes only",
     "kc_and_feedback", ["feedback", "sentiment"]),
    ("tax", "Tax and compliance attributes only",
     "tax_and_insurance", ["tax", "tax / compliance"]),
    ("insurance", "Insurance attributes only",
     "tax_and_insurance", ["insurance"]),
]

NEW_NAMES = [n for n, _, _, _ in NEW_AGENTS]


def _now():
    return datetime.now(timezone.utc)


def upgrade() -> None:
    conn = op.get_bind()
    for name, description, source_name, groups in NEW_AGENTS:
        existing = conn.execute(
            sa.select(agents_tbl.c.id).where(agents_tbl.c.name == name)
        ).fetchone()
        if existing is not None:
            agent_id = existing[0]
        else:
            src = conn.execute(
                sa.select(
                    agents_tbl.c.kind,
                    agents_tbl.c.system_instruction,
                    agents_tbl.c.prompt,
                    agents_tbl.c.input_types,
                ).where(agents_tbl.c.name == source_name)
            ).fetchone()
            if src is not None:
                kind = src[0] or "extraction"
                system_instruction = src[1] or ""
                prompt = src[2] or ""
                input_types = src[3]
                if input_types is None:
                    input_types = ["transcription"]
                # psycopg may return JSON as str; normalise to a list.
                if isinstance(input_types, str):
                    try:
                        import json as _json

                        parsed = _json.loads(input_types)
                        input_types = parsed if isinstance(parsed, list) else ["transcription"]
                    except Exception:
                        input_types = ["transcription"]
                if not isinstance(input_types, list):
                    input_types = ["transcription"]
            else:
                kind = "extraction"
                system_instruction = ""
                prompt = ""
                input_types = ["transcription"]
            agent_id = uuid.uuid4().hex
            conn.execute(
                agents_tbl.insert().values(
                    id=agent_id,
                    name=name,
                    description=description,
                    kind=kind,
                    system_instruction=system_instruction,
                    prompt=prompt,
                    input_types=input_types,
                    is_enabled=True,
                    created_at=_now(),
                )
            )
        # Link attributes whose group matches (strip + case-insensitive).
        wanted = {g.strip().lower() for g in groups}
        try:
            attr_rows = conn.execute(sa.select(
                attributes_tbl.c.id, attributes_tbl.c.group_name)).fetchall()
        except Exception:
            attr_rows = []
        for attr_id, group_name in attr_rows:
            try:
                norm = str(group_name or "").strip().lower()
            except Exception:
                continue
            if norm not in wanted:
                continue
            already = conn.execute(
                sa.select(agent_attributes_tbl.c.agent_id).where(
                    (agent_attributes_tbl.c.agent_id == agent_id)
                    & (agent_attributes_tbl.c.attribute_id == attr_id)
                )
            ).fetchone()
            if already is None:
                conn.execute(
                    agent_attributes_tbl.insert().values(
                        agent_id=agent_id, attribute_id=attr_id)
                )


def downgrade() -> None:
    conn = op.get_bind()
    ids = conn.execute(
        sa.select(agents_tbl.c.id).where(agents_tbl.c.name.in_(NEW_NAMES))
    ).fetchall()
    id_list = [r[0] for r in ids]
    if id_list:
        conn.execute(
            agent_attributes_tbl.delete().where(
                agent_attributes_tbl.c.agent_id.in_(id_list))
        )
        conn.execute(
            agents_tbl.delete().where(agents_tbl.c.id.in_(id_list))
        )
