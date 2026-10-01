"""Agents config module: system instruction + prompt per agent."""

from pydantic import BaseModel, Field


class AgentCreate(BaseModel):
    name: str
    system_instruction: str = ""
    prompt: str = ""
    input_types: list[str] = Field(default_factory=list)


class AgentOut(AgentCreate):
    id: str
