"""Attributes config module: typed fields mapped to one agent."""

from pydantic import BaseModel, Field


class AttributeCreate(BaseModel):
    agent_id: str
    name: str
    type: str = "string"
    description: str = ""
    json_schema: dict = Field(default_factory=dict)
    required: bool = False


class AttributeUpdate(BaseModel):
    agent_id: str | None = None
    name: str | None = None
    type: str | None = None
    description: str | None = None
    json_schema: dict | None = None
    required: bool | None = None


class AttributeOut(AttributeCreate):
    id: str
