from datetime import datetime
from typing import Any, Literal
from urllib.parse import parse_qsl, unquote, urlsplit
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)


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


class ReplayOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: StrictStr | None = Field(default=None, min_length=1, max_length=120)
    temperature: float | None = Field(default=None, strict=True, ge=0, le=2)
    top_p: float | None = Field(default=None, strict=True, ge=0, le=1)
    seed: StrictInt | None = Field(default=None, ge=0)
    max_tokens: StrictInt | None = Field(default=None, ge=1)

    @model_validator(mode="before")
    @classmethod
    def explicit_nulls_rejected(cls, data):
        if isinstance(data, dict):
            for field in cls.model_fields:
                if field in data and data[field] is None:
                    raise ValueError(f"{field} 不能为 null")
        return data


class ReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool
    overrides: ReplayOverrides = Field(default_factory=ReplayOverrides)

    @field_validator("confirm", mode="before")
    @classmethod
    def confirmation_must_be_literal_true(cls, value):
        if value is not True:
            raise ValueError("confirm 必须为布尔值 true")
        return value


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


SENSITIVE_QUERY_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "key",
        "token",
        "access_token",
        "authorization",
        "auth",
        "secret",
        "password",
        "credential",
    }
)


PUBLIC_QUERY_VALUE_KEYS = frozenset(
    {"tenant", "mode", "region", "project", "namespace", "api-version"}
)
SENSITIVE_QUERY_MARKERS = (
    "token",
    "secret",
    "password",
    "credential",
    "signature",
    "authorization",
)


def normalized_query_key(key: str) -> str:
    return unquote(key).casefold().replace("-", "_")


def is_sensitive_query_key(key: str) -> bool:
    normalized = normalized_query_key(key)
    return (
        normalized in SENSITIVE_QUERY_KEYS
        or normalized in {"sig", "code", "jwt", "assertion", "session", "session_id"}
        or normalized.endswith("_key")
        or any(marker in normalized for marker in SENSITIVE_QUERY_MARKERS)
    )


def has_sensitive_query(query: str) -> bool:
    return any(
        is_sensitive_query_key(key)
        for key, _ in parse_qsl(query, keep_blank_values=True)
    )


def redact_url_credentials(value: str) -> str:
    """Return a public-safe URL without userinfo, fragments, or unchecked query values.

    Only a small allowlist of display-safe query values is preserved. Unknown keys
    are masked by default so legacy OAuth/signed URLs cannot echo credentials.
    """
    if not value:
        return value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    netloc = parsed.netloc
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]
    segments = []
    for segment in parsed.query.split("&"):
        if not segment:
            continue
        key, _, _ = segment.partition("=")
        if unquote(key).casefold() not in PUBLIC_QUERY_VALUE_KEYS:
            segments.append(f"{key}=REDACTED")
        else:
            segments.append(segment)
    return parsed._replace(
        netloc=netloc, query="&".join(segments), fragment=""
    ).geturl()


def http_url(value: str, *, allow_blank: bool = False, allow_query: bool = False) -> str:
    value = value.strip()
    if allow_blank and not value:
        return value
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("必须是有效的 http(s) 地址，且不能包含用户名或密码")
    if parsed.fragment or (parsed.query and not allow_query):
        raise ValueError("地址不能包含查询参数或片段")
    if allow_query and has_sensitive_query(parsed.query):
        raise ValueError("MCP 地址不能包含凭据查询参数")
    return value.rstrip("/")


class SettingsPatch(BaseModel):
    default_upstream_base: str | None = None
    default_upstream_model: str | None = None
    default_upstream_key: str | None = None
    default_mcp_upstream: str | None = None
    default_mcp_key: str | None = None
    enable_llm_check: bool | None = None
    check_model_base: str | None = None
    check_model_key: str | None = None
    check_model_name: str | None = None
    check_model_timeout: float | None = Field(default=None, ge=1, le=300)
    gateway_daily_token_budget: int | None = Field(default=None, ge=0, le=10**12)
    gateway_failure_threshold: int | None = Field(default=None, ge=1, le=20)
    gateway_cooldown_seconds: int | None = Field(default=None, ge=1, le=3600)
    default_input_price_per_million: float | None = Field(default=None, ge=0, le=10**6)
    default_output_price_per_million: float | None = Field(default=None, ge=0, le=10**6)

    @model_validator(mode="before")
    @classmethod
    def non_secret_fields_cannot_be_null(cls, data):
        if isinstance(data, dict):
            secret_fields = {"default_upstream_key", "default_mcp_key", "check_model_key"}
            for field, value in data.items():
                if field in cls.model_fields and field not in secret_fields and value is None:
                    raise ValueError(f"{field} 不能为 null")
        return data

    @field_validator("default_upstream_base", "check_model_base")
    @classmethod
    def required_http_url(cls, value: str | None) -> str | None:
        return http_url(value) if value is not None else None

    @field_validator("default_mcp_upstream")
    @classmethod
    def optional_http_url(cls, value: str | None) -> str | None:
        return http_url(value, allow_blank=True, allow_query=True) if value is not None else None


class SettingsTestIn(BaseModel):
    kind: Literal["openai", "check", "mcp"]


class RouteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    match_model: str = Field(min_length=1, max_length=120)
    target_model: str = Field(min_length=1, max_length=120)
    upstream_base: str
    api_key: str | None = None
    priority: int = Field(default=100, ge=0, le=100000)
    enabled: bool = True
    input_price_per_million: float = Field(default=0, ge=0, le=10**6)
    output_price_per_million: float = Field(default=0, ge=0, le=10**6)

    @field_validator("name", "match_model", "target_model")
    @classmethod
    def route_text_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value

    @field_validator("upstream_base")
    @classmethod
    def route_http_url(cls, value: str) -> str:
        return http_url(value)


class RoutePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    match_model: str | None = Field(default=None, min_length=1, max_length=120)
    target_model: str | None = Field(default=None, min_length=1, max_length=120)
    upstream_base: str | None = None
    api_key: str | None = None
    priority: int | None = Field(default=None, ge=0, le=100000)
    enabled: bool | None = None
    input_price_per_million: float | None = Field(default=None, ge=0, le=10**6)
    output_price_per_million: float | None = Field(default=None, ge=0, le=10**6)

    @model_validator(mode="before")
    @classmethod
    def non_secret_fields_cannot_be_null(cls, data):
        if isinstance(data, dict):
            for field, value in data.items():
                if field in cls.model_fields and field != "api_key" and value is None:
                    raise ValueError(f"{field} 不能为 null")
        return data

    @field_validator("name", "match_model", "target_model")
    @classmethod
    def optional_route_text_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value

    @field_validator("upstream_base")
    @classmethod
    def optional_route_http_url(cls, value: str | None) -> str | None:
        return http_url(value) if value is not None else None
