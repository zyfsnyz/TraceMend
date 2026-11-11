from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    db_url: str = "sqlite:///./data/agenttrace.db"
    default_upstream_base: str = "https://api.openai.com/v1"
    default_mcp_upstream: str | None = None
    admin_key: str | None = None
