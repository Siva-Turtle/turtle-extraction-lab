"""Test module: run selected agents over input data via an OpenRouter model."""

from pydantic import BaseModel, Field


class AttributeResult(BaseModel):
    value: str | float | int | bool | None = None
    confidence: float = 0.0
    confidence_type: str = "quoted"
    evidence: str = ""


class RunCreate(BaseModel):
    input_type: str  # transcription | messages | mail
    input_data: str
    agent_ids: list[str] = Field(default_factory=list)
    model: str


class FeedbackCreate(BaseModel):
    agent_name: str = ""
    attribute_name: str = ""
    rating: str  # up | down
    remarks: str = ""
