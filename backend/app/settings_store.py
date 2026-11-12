import json
import os
import re
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import dotenv_values

from .config import Settings
from .schemas import redact_url_credentials

ENV_KEY_BY_FIELD = {
    "default_upstream_base": "DEFAULT_UPSTREAM_BASE",
    "default_upstream_model": "DEFAULT_UPSTREAM_MODEL",
    "default_upstream_key": "DEFAULT_UPSTREAM_KEY",
    "default_mcp_upstream": "DEFAULT_MCP_UPSTREAM",
    "default_mcp_key": "DEFAULT_MCP_KEY",
    "enable_llm_check": "ENABLE_LLM_CHECK",
    "check_model_base": "CHECK_MODEL_BASE",
    "check_model_key": "CHECK_MODEL_KEY",
    "check_model_name": "CHECK_MODEL_NAME",
    "check_model_timeout": "CHECK_MODEL_TIMEOUT",
    "gateway_daily_token_budget": "GATEWAY_DAILY_TOKEN_BUDGET",
    "gateway_failure_threshold": "GATEWAY_FAILURE_THRESHOLD",
    "gateway_cooldown_seconds": "GATEWAY_COOLDOWN_SECONDS",
    "default_input_price_per_million": "DEFAULT_INPUT_PRICE_PER_MILLION",
    "default_output_price_per_million": "DEFAULT_OUTPUT_PRICE_PER_MILLION",
}

_ENV_LOCK = threading.RLock()


def env_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def update_env_file(path: Path, updates: dict[str, Any]) -> None:
    with _ENV_LOCK:
        _update_env_file_unlocked(path, updates)


def _update_env_file_unlocked(path: Path, updates: dict[str, Any]) -> None:
    """Atomically update selected keys while preserving comments and unrelated lines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = {key: env_text(value) for key, value in updates.items()}
    output = []
    seen = set()
    for line in lines:
        matched = re.match(
            r"^(\s*)(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)(\s*=)", line
        )
        if matched and matched.group(2) in updates:
            key = matched.group(2)
            if key in seen:
                continue
            value = remaining.pop(key)
            seen.add(key)
            output.append(f"{key}={json.dumps(value, ensure_ascii=True)}")
        else:
            output.append(line)
    for key, value in remaining.items():
        output.append(f"{key}={json.dumps(value, ensure_ascii=True)}")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid4().hex}")
    try:
        temporary.write_text("\n".join(output) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    for key, value in updates.items():
        os.environ[key] = env_text(value)


def read_env_value(path: Path, key: str) -> str | None:
    if key in os.environ:
        return os.environ[key]
    if not path.exists():
        return None
    return dotenv_values(path).get(key)


def public_settings(settings: Settings) -> dict[str, Any]:
    return {
        "default_upstream_base": redact_url_credentials(settings.default_upstream_base),
        "default_upstream_model": settings.default_upstream_model or "",
        "default_upstream_key_configured": bool(settings.default_upstream_key),
        "default_mcp_upstream": redact_url_credentials(settings.default_mcp_upstream or ""),
        "default_mcp_key_configured": bool(settings.default_mcp_key),
        "enable_llm_check": settings.enable_llm_check,
        "check_model_base": redact_url_credentials(settings.check_model_base),
        "check_model_key_configured": bool(settings.check_model_key),
        "check_model_name": settings.check_model_name,
        "check_model_timeout": settings.check_model_timeout,
        "gateway_daily_token_budget": settings.gateway_daily_token_budget,
        "gateway_failure_threshold": settings.gateway_failure_threshold,
        "gateway_cooldown_seconds": settings.gateway_cooldown_seconds,
        "default_input_price_per_million": settings.default_input_price_per_million,
        "default_output_price_per_million": settings.default_output_price_per_million,
    }


def save_settings(application, values: dict[str, Any]) -> Settings:
    """Read, merge, validate, persist and publish settings as one atomic step.

    The whole read-modify-write sequence runs under ``_ENV_LOCK`` (a re-entrant
    lock, so ``update_env_file`` re-acquires it safely). Without that, two
    concurrent saves of different fields each start from the same snapshot and
    the later writer silently drops the other field.
    """
    secret_fields = {"default_upstream_key", "default_mcp_key", "check_model_key"}
    changes = {
        field: value
        for field, value in values.items()
        if field in ENV_KEY_BY_FIELD
        and not (field in secret_fields and value is None)
    }
    with _ENV_LOCK:
        candidate_values = application.state.settings.model_dump()
        candidate_values.update(changes)
        settings = Settings(_env_file=None, **candidate_values)
        updates = {ENV_KEY_BY_FIELD[field]: value for field, value in changes.items()}
        if updates:
            update_env_file(application.state.env_path, updates)
        application.state.settings = settings
    return settings
