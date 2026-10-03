"""Test module: run selected agents over input data via an OpenRouter model."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.core.openrouter import REASONING_EFFORTS


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
    # OpenRouter reasoning effort ("" = not sent, model default).
    reasoning_effort: str = ""
    # Multi-model compare group key: "" (single) or 32 lowercase hex chars.
    run_group_id: str = ""
    # Per-agent reuse: {"<agent_id>": "<source log_id>"} — agents listed here
    # are NOT sent to the model; output + per-agent usage are copied.
    # Invalid entries are ignored silently (that agent runs normally).
    reuse: dict = Field(default_factory=dict)

    @field_validator("reuse", mode="before")
    @classmethod
    def _coerce_reuse(cls, v):
        return v if isinstance(v, dict) else {}

    @field_validator("run_group_id", mode="before")
    @classmethod
    def _coerce_run_group_id(cls, v):
        import re
        if not isinstance(v, str):
            return ""
        s = v.strip()
        if not s:
            return ""
        if not re.fullmatch(r"[0-9a-f]{32}", s):
            raise ValueError("run_group_id must be 32 lowercase hex chars")
        return s

    @field_validator("filters", mode="before")
    @classmethod
    def _coerce_filters(cls, v):
        return v if isinstance(v, dict) else {}

    @field_validator("reasoning_effort", mode="before")
    @classmethod
    def _coerce_reasoning_effort(cls, v):
        s = v.strip() if isinstance(v, str) else ""
        if s and s not in REASONING_EFFORTS:
            raise ValueError(
                "reasoning_effort must be max|xhigh|high|medium|low|minimal|none")
        return s


class CheckModelSlot(BaseModel):
    model: str
    reasoning_effort: str = ""

    @field_validator("reasoning_effort", mode="before")
    @classmethod
    def _coerce_reasoning_effort(cls, v):
        s = v.strip() if isinstance(v, str) else ""
        if s and s not in REASONING_EFFORTS:
            raise ValueError(
                "reasoning_effort must be max|xhigh|high|medium|low|minimal|none")
        return s


class CheckExistingIn(BaseModel):
    input_type: str = ""
    input_data: str = ""
    meeting_id: str = ""
    agent_ids: list[str] = Field(default_factory=list)
    filters: dict = Field(default_factory=dict)
    client: str = ""
    meeting_type: str = ""
    meeting_title: str = ""
    models: list[CheckModelSlot] = Field(min_length=1, max_length=4)

    @field_validator("filters", mode="before")
    @classmethod
    def _coerce_filters(cls, v):
        return v if isinstance(v, dict) else {}


class CheckExistingAgentEntry(BaseModel):
    agent_id: str
    agent_name: str = ""
    log_id: str
    created_at: str
    cost_usd: float | None = None
    duration_ms: float | None = None


class CheckExistingSlot(BaseModel):
    model: str
    reasoning_effort: str = ""
    agents: list[CheckExistingAgentEntry] = Field(default_factory=list)


class CheckExistingOut(BaseModel):
    slots: list[CheckExistingSlot] = Field(default_factory=list)


class CheckExistingAutoSlot(BaseModel):
    model: str
    reasoning_effort: str = ""
    log: dict | None = None
    agents: list[CheckExistingAgentEntry] = Field(default_factory=list)


class CheckExistingAutoOut(BaseModel):
    slots: list[CheckExistingAutoSlot] = Field(default_factory=list)


class ReuseRunIn(BaseModel):
    log_id: str = ""
    run_group_id: str = ""

    @field_validator("run_group_id", mode="before")
    @classmethod
    def _coerce_run_group_id(cls, v):
        import re
        if not isinstance(v, str):
            return ""
        s = v.strip()
        if not s:
            return ""
        if not re.fullmatch(r"[0-9a-f]{32}", s):
            raise ValueError("run_group_id must be 32 lowercase hex chars")
        return s


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
    rating: str  # up | down | "" ("" = clear)
    remarks: str | None = None

    @field_validator("rating", mode="before")
    @classmethod
    def _check_rating(cls, v):
        s = v.strip() if isinstance(v, str) else v
        if s not in ("up", "down", ""):
            raise ValueError("rating must be up|down|''")
        return s


class FeedbackOut(BaseModel):
    id: str
    run_id: str
    agent_name: str = ""
    attribute_name: str = ""
    rating: str
    remarks: str = ""


class BatchFeedbackItem(BaseModel):
    run_id: str
    agent_name: str = ""
    attribute_name: str = ""
    rating: str  # up | down | "" ("" = clear)
    remarks: str | None = None

    @field_validator("rating", mode="before")
    @classmethod
    def _check_rating(cls, v):
        s = v.strip() if isinstance(v, str) else v
        if s not in ("up", "down", ""):
            raise ValueError("rating must be up|down|''")
        return s


class BatchFeedbackIn(BaseModel):
    items: list[BatchFeedbackItem] = Field(default_factory=list)
    only_unrated: bool = False


class RetryAgentIn(BaseModel):
    agent_id: str = ""
