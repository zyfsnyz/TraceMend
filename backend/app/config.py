import os
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_PROJECT_ENV = Path(__file__).resolve().parents[2] / ".env"
PROJECT_ENV = Path(os.environ.get("AGENTTRACE_ENV_FILE", DEFAULT_PROJECT_ENV))
BOOTSTRAP_ENV = Path(os.environ.get("AGENTTRACE_BOOTSTRAP_ENV", DEFAULT_PROJECT_ENV))


def env_files() -> tuple[Path, ...]:
    return (PROJECT_ENV,) if PROJECT_ENV == BOOTSTRAP_ENV else (BOOTSTRAP_ENV, PROJECT_ENV)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=env_files(), extra="ignore")

    db_url: str = "sqlite:///./data/agenttrace.db"
    default_upstream_base: str = "https://api.openai.com/v1"
    default_upstream_model: str | None = None
    default_upstream_key: str | None = None
    default_mcp_upstream: str | None = None
    default_mcp_key: str | None = None
    admin_key: str | None = None
    enable_llm_check: bool = False
    check_model_base: str = "https://api.openai.com/v1"
    check_model_key: str | None = None
    check_model_name: str = "gpt-4o-mini"
    check_model_timeout: float = 60.0
    gateway_daily_token_budget: int = 0
    gateway_failure_threshold: int = 2
    gateway_cooldown_seconds: int = 30
    default_input_price_per_million: float = 0.0
    default_output_price_per_million: float = 0.0

    @field_validator("admin_key")
    @classmethod
    def admin_key_must_be_ascii(cls, value: str | None) -> str | None:
        if value and not value.isascii():
            raise ValueError("ADMIN_KEY 只能使用 ASCII 字符（HTTP 请求头限制）")
        return value
