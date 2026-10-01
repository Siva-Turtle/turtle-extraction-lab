"""Meetings picker schemas: clients, titles, meetings, transcript."""

from pydantic import BaseModel


class ClientOut(BaseModel):
    id: str
    name: str


class TitlesOut(BaseModel):
    titles: list[str]


class MeetingOut(BaseModel):
    id: str
    title: str
    date: str
    duration_min: float | None = None
    participants: list[str] = []


class MeetingsOut(BaseModel):
    meetings: list[MeetingOut]


class TranscriptOut(BaseModel):
    id: str
    title: str
    date: str
    transcription: str
    # Always scrubbed server-side; defaults True so old clients still parse.
    scrubbed: bool = True
