"""Tables: agents + attributes (config), runs + feedbacks (tests), run_logs (denormalized)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, ForeignKey, JSON, Boolean, DateTime, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


def _id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    system_instruction: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # DORMANT: `prompt` is dead (runs use scrubbed transcription + attributes).
    # Column kept so legacy rows still load; never referenced by the API.
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    input_types: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# Many-to-many: one attribute can belong to multiple agents.
agent_attributes = Table(
    "agent_attributes",
    Base.metadata,
    Column("agent_id", String(36), ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True),
    Column("attribute_id", String(36), ForeignKey("attributes.id", ondelete="CASCADE"), primary_key=True),
)


class Attribute(Base):
    __tablename__ = "attributes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False, default="string")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    enum_values: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Ordered sub-fields for type=="object": [{name, type, null_allowed}].
    # type in [string, number, boolean, array]; kept [] for all other types.
    object_properties: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Free-text grouping label (CSV Group column). Column is `group_name`
    # because `group` is SQL-reserved; API field is `group`.
    group_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # Item-shape config for type=="array":
    # {kind: string|number|object, properties: [{name, type, null_allowed}]}.
    # kind string|number keeps properties []; kind object requires non-empty
    # properties. Kept as default string-kind for all other types (ignored).
    array_items: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    input_type: Mapped[str] = mapped_column(String(32), nullable=False)
    input_data: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    agent_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    outputs: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Feedback(Base):
    __tablename__ = "feedbacks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("runs.id"), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    attribute_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    rating: Mapped[str] = mapped_column(String(16), nullable=False)  # up | down
    remarks: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RunLog(Base):
    """Denormalized snapshot per run — deliberately NO FK to agents/attributes."""

    __tablename__ = "run_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    input_type: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    input_data: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    agent_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    attribute_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    outputs: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    feedback: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    usage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    filters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    requests: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
