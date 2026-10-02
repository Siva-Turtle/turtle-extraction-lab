"""Agents config module: system instruction + input types per agent."""

from pydantic import BaseModel, Field


class AgentCreate(BaseModel):
    name: str
    description: str = ""
    kind: str = "extraction"
    system_instruction: str = ""
    input_types: list[str] = Field(default_factory=list)
    is_enabled: bool = True


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    kind: str | None = None
    system_instruction: str | None = None
    input_types: list[str] | None = None
    is_enabled: bool | None = None
    attribute_ids: list[str] | None = None


class AgentOut(BaseModel):
    id: str
    name: str
    description: str = ""
    kind: str = "extraction"
    system_instruction: str = ""
    input_types: list[str] = Field(default_factory=list)
    is_enabled: bool = True
