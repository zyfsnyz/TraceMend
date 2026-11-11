from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ENV = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(PROJECT_ENV, ".env"), extra="ignore")

    db_url: str = "sqlite:///./data/agenttrace.db"
    default_upstream_base: str = "https://api.openai.com/v1"
    default_mcp_upstream: str | None = None
    admin_key: str | None = None
    enable_llm_check: bool = False
    check_model_base: str = "https://api.openai.com/v1"
    check_model_key: str | None = None
    check_model_name: str = "gpt-4o-mini"
    check_model_timeout: float = 60.0

    @field_validator("admin_key")
    @classmethod
    def admin_key_must_be_ascii(cls, value: str | None) -> str | None:
        if value and not value.isascii():
            raise ValueError("ADMIN_KEY 只能使用 ASCII 字符（HTTP 请求头限制）")
        return value
