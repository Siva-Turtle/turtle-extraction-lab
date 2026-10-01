"""Agents config module: system instruction + prompt per agent."""

from pydantic import BaseModel, Field


class AgentCreate(BaseModel):
    name: str
    system_instruction: str = ""
    prompt: str = ""
    input_types: list[str] = Field(default_factory=list)
    is_enabled: bool = True


class AgentUpdate(BaseModel):
    name: str | None = None
    system_instruction: str | None = None
    prompt: str | None = None
    input_types: list[str] | None = None
    is_enabled: bool | None = None


class AgentOut(AgentCreate):
    id: str
