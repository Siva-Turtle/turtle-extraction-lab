"""Attributes config module: typed fields mapped to one agent."""

from pydantic import BaseModel, Field


class AttributeCreate(BaseModel):
    agent_id: str
    name: str
    type: str = "string"
    description: str = ""
    json_schema: dict = Field(default_factory=dict)
    required: bool = False


class AttributeOut(AttributeCreate):
    id: str
