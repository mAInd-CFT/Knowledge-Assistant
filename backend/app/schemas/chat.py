from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionResponse(APIModel):
    session_id: UUID
    created_at: datetime
    title: str = "Новый диалог"


class ChatRequest(APIModel):
    message: str = Field(min_length=1, max_length=10_000)
    session_id: UUID | None = None
    request_id: UUID = Field(default_factory=uuid4)


class Source(APIModel):
    title: str
    url: str | None = None
    file_name: str | None = None
    source_id: str | None = None
    page: str | None = None
    section: str | None = None
    metadata: dict[str, Any] | None = None


class ChatResponse(APIModel):
    answer: str
    session_id: UUID
    sources: list[Source] = Field(default_factory=list)
    rag_enabled: bool = True
    needs_clarification: bool = False
    processing_time_ms: int | None = None


class MessageResponse(APIModel):
    message_id: UUID
    session_id: UUID
    role: str
    content: str
    sources: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime


class HealthResponse(APIModel):
    status: str
    app: str
    version: str
