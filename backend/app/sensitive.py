import re
from typing import Any

REDACTED = "[已隐藏]"
SENSITIVE_KEY = re.compile(
    r"(^|[_-])(authorization|api[_-]?key|apikey|secret|password|passwd|admin[_-]?key|"
    r"access[_-]?token|refresh[_-]?token)([_-]|$)",
    re.IGNORECASE,
)
SENSITIVE_TEXT = (
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{6,}"), f"Bearer {REDACTED}"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), REDACTED),
)


def redact_text(value: str) -> str:
    for pattern, replacement in SENSITIVE_TEXT:
        value = pattern.sub(replacement, value)
    return value


def redact_sensitive(value: Any) -> Any:
    """Strip credentials from audit artefacts without changing ordinary evidence."""
    if isinstance(value, dict):
        return {
            key: REDACTED
            if SENSITIVE_KEY.search(str(key))
            else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
