from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class CommentCreate(BaseModel):
    author: str = Field(min_length=1, max_length=80)
    body: str = Field(min_length=1, max_length=4000)
    status: Literal["open", "resolved"] = "open"

    @field_validator("author", "body")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value


class TraceCommentIn(CommentCreate):
    span_id: str | None = None


class CommentPatch(BaseModel):
    author: str | None = Field(default=None, min_length=1, max_length=80)
    body: str | None = Field(default=None, min_length=1, max_length=4000)
    status: Literal["open", "resolved"] | None = None

    @field_validator("author", "body")
    @classmethod
    def optional_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value

    @model_validator(mode="after")
    def has_change(self):
        if self.author is None and self.body is None and self.status is None:
            raise ValueError("至少提供一个待更新字段")
        return self
