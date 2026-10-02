"""Test module: run selected agents over input data via an OpenRouter model."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class AttributeResult(BaseModel):
    value: str | float | int | bool | None = None
    confidence: float = 0.0
    confidence_type: str = "quoted"
    evidence: str = ""


class RunCreate(BaseModel):
    input_type: str = ""  # transcription | messages | mail (ignored when meeting_id wins)
    input_data: str = ""  # ignored when meeting_id present — never trust client text
    meeting_id: str = ""  # when non-blank, server re-fetches + scrubs the transcript
    agent_ids: list[str] = Field(default_factory=list)
    model: str
    filters: dict = Field(default_factory=dict)
    # Denormalized meeting snapshot (plain strings, never FKs): captured at
    # run time from the Test Lab picker — client name, the meeting-title
    # filter (type-like grouping), and the specific meeting instance title.
    client: str = ""
    meeting_type: str = ""
    meeting_title: str = ""

    @field_validator("filters", mode="before")
    @classmethod
    def _coerce_filters(cls, v):
        return v if isinstance(v, dict) else {}


class RunOut(BaseModel):
    id: str
    input_type: str
    model: str
    agent_ids: list[str] = Field(default_factory=list)
    created_at: datetime


class RunDetail(RunOut):
    input_data: str
    outputs: dict = Field(default_factory=dict)


class FeedbackCreate(BaseModel):
    agent_name: str = ""
    attribute_name: str = ""
    rating: str  # up | down
    remarks: str = ""


class FeedbackOut(FeedbackCreate):
    id: str
    run_id: str
