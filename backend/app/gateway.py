import fnmatch
import threading
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from .models import GatewayRoute
from .schemas import redact_url_credentials
from .settings_store import read_env_value


@dataclass
class UpstreamCandidate:
    upstream_base: str
    target_model: str | None
    route: GatewayRoute | None = None
    api_key: str | None = None
    explicit: bool = False

    @property
    def circuit_key(self) -> str:
        return self.route.id if self.route else "default"


class CircuitRegistry:
    def __init__(self):
        self._states: dict[str, dict[str, float | int]] = {}
        self._lock = threading.Lock()

    def snapshot(self, key: str) -> dict[str, Any]:
        with self._lock:
            state = dict(self._states.get(key, {"failures": 0, "open_until": 0.0}))
        remaining = max(0.0, float(state["open_until"]) - time.monotonic())
        return {
            "status": "open" if remaining > 0 else "closed",
            "failures": int(state["failures"]),
            "open_remaining_seconds": round(remaining, 1),
        }

    def is_open(self, key: str) -> bool:
        return self.snapshot(key)["status"] == "open"

    def record_failure(self, key: str, threshold: int, cooldown_seconds: int) -> None:
        with self._lock:
            state = self._states.setdefault(key, {"failures": 0, "open_until": 0.0})
            state["failures"] = int(state["failures"]) + 1
            if state["failures"] >= threshold:
                state["open_until"] = time.monotonic() + cooldown_seconds

    def record_success(self, key: str) -> None:
        with self._lock:
            self._states.pop(key, None)

    def reset(self, key: str) -> None:
        self.record_success(key)


def route_dict(route: GatewayRoute, application) -> dict[str, Any]:
    key_configured = bool(
        route.key_name and read_env_value(application.state.env_path, route.key_name)
    )
    return {
        "id": route.id,
        "name": route.name,
        "match_model": route.match_model,
        "target_model": route.target_model,
        "upstream_base": redact_url_credentials(route.upstream_base),
        "api_key_configured": key_configured,
        "priority": route.priority,
        "enabled": route.enabled,
        "input_price_per_million": route.input_price_per_million,
        "output_price_per_million": route.output_price_per_million,
        "circuit": application.state.circuits.snapshot(route.id),
    }


def load_candidates(
    session,
    settings,
    env_path,
    requested_model: object,
    explicit_upstream_base: str | None = None,
) -> list[UpstreamCandidate]:
    requested = requested_model if isinstance(requested_model, str) else None
    if explicit_upstream_base:
        return [
            UpstreamCandidate(
                upstream_base=explicit_upstream_base,
                target_model=requested,
                api_key=None,
                explicit=True,
            )
        ]
    routes = session.scalars(
        select(GatewayRoute)
        .where(GatewayRoute.enabled.is_(True))
        .order_by(GatewayRoute.priority, GatewayRoute.created_at, GatewayRoute.id)
    ).all()
    effective_model = requested or settings.default_upstream_model
    candidates = []
    for route in routes:
        if effective_model and fnmatch.fnmatchcase(effective_model, route.match_model):
            candidates.append(
                UpstreamCandidate(
                    upstream_base=route.upstream_base,
                    target_model=route.target_model,
                    route=route,
                    api_key=(
                        read_env_value(env_path, route.key_name) if route.key_name else None
                    ),
                )
            )
    if candidates:
        return candidates
    return [
        UpstreamCandidate(
            upstream_base=settings.default_upstream_base,
            target_model=effective_model,
            api_key=settings.default_upstream_key,
        )
    ]


def usage_cost(candidate: UpstreamCandidate, settings, usage: object) -> float:
    if not isinstance(usage, dict):
        return 0.0
    if candidate.route:
        input_price = candidate.route.input_price_per_million
        output_price = candidate.route.output_price_per_million
    else:
        input_price = settings.default_input_price_per_million
        output_price = settings.default_output_price_per_million
    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    if not isinstance(prompt, (int, float)) or isinstance(prompt, bool):
        prompt = 0
    if not isinstance(completion, (int, float)) or isinstance(completion, bool):
        completion = 0
    return round((prompt * input_price + completion * output_price) / 1_000_000, 10)


def gateway_metadata(
    candidate: UpstreamCandidate,
    settings,
    requested_model: object,
    attempts: list[dict[str, Any]],
    output: object,
) -> dict[str, Any]:
    usage = output.get("usage", {}) if isinstance(output, dict) else {}
    return {
        "requested_model": requested_model if isinstance(requested_model, str) else None,
        "route_id": candidate.route.id if candidate.route else None,
        "route_name": candidate.route.name if candidate.route else "默认上游",
        "target_model": candidate.target_model,
        "attempts": attempts,
        "cost_usd": usage_cost(candidate, settings, usage),
    }
