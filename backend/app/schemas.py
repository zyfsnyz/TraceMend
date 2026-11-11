from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class SpanIn(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(default_factory=lambda: str(uuid4()))
    parent_span_id: str | None = None
    sequence: int = 0
    kind: Literal["user", "llm", "tool", "tool_result", "system", "mcp"] = "system"
    name: str = ""
    status: str = "completed"
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: float | None = None
    input: Any = None
    output: Any = None
    content: str | None = None
    reasoning: str | None = None
    tool_name: str | None = None
    tool_arguments: Any = None
    tool_result: Any = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    error: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceIn(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(default_factory=lambda: str(uuid4()))
    parent_id: str | None = None
    name: str = "Agent 请求"
    source: str = "unknown"
    status: str = "completed"
    model: str | None = None
    input: Any = None
    output: Any = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: float | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    tags: list[Any] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: Any = None


class IngestIn(BaseModel):
    trace: TraceIn
    spans: list[SpanIn] = Field(default_factory=list)
