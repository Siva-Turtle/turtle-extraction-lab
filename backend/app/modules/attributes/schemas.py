"""Attributes config module: typed fields shared across agents (many-to-many)."""

from pydantic import BaseModel, Field

ATTRIBUTE_TYPES = ("string", "number", "boolean", "enum")


class AttributeCreate(BaseModel):
    agent_ids: list[str] = Field(min_length=1)
    name: str
    type: str = "string"
    description: str = ""
    enum_values: list[str] = Field(default_factory=list)


class AttributeUpdate(BaseModel):
    agent_ids: list[str] | None = None
    name: str | None = None
    type: str | None = None
    description: str | None = None
    enum_values: list[str] | None = None


class AttributeOut(BaseModel):
    id: str
    agent_ids: list[str] = Field(default_factory=list)
    name: str
    type: str = "string"
    description: str = ""
    enum_values: list[str] = Field(default_factory=list)
