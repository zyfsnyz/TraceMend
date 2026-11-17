import hashlib
import json
import re
from contextlib import suppress
from copy import deepcopy
from datetime import UTC, datetime
from difflib import SequenceMatcher, unified_diff
from uuid import UUID

import anyio
import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from .async_db import run_db
from .audit import begin_write_transaction, load_detail
from .gateway import gateway_metadata, load_candidates
from .models import ReplayReservation, Span, Trace
from .proxy import (
    SSEAccumulator,
    attempt_info,
    close_attempt_resources,
    persist_openai_trace,
    response_json,
    retryable_upstream,
    sse_error_evidence,
)
from .schemas import ReplayRequest
from .serializers import span_dict, trace_dict
from .usage import daily_token_usage

router = APIRouter(prefix="/api")

ATTRIBUTION_NOTE = "单次一致或差异均不能单独证明模型随机性或幻觉成因；请结合多次实验与事实评估。"
_CREDENTIAL_KEYS = {
    "authorization",
    "proxy_authorization",
    "api_key",
    "apikey",
    "x_api_key",
    "access_token",
    "refresh_token",
    "id_token",
    "client_secret",
    "secret",
    "password",
    "passwd",
    "passphrase",
    "credential",
    "credentials",
    "cookie",
    "set_cookie",
    "token",
    "key",
    "auth",
    "bearer",
    "admin_key",
    "admin_token",
    "auth_token",
    "bearer_token",
    "session_token",
    "private_key",
    "secret_key",
    "access_key",
    "secret_access_key",
    "api_token",
    "access_key_id",
    "auth_key",
    "session_key",
    "x_api_token",
}
_CREDENTIAL_SUFFIXES = (
    "_api_key",
    "_access_token",
    "_refresh_token",
    "_secret",
    "_password",
    "_passwd",
    "_credential",
    "_authorization",
    "_auth_token",
    "_bearer_token",
    "_private_key",
    "_access_key",
    "_secret_key",
    "_admin_key",
    "_token",
    "_key",
    "_key_id",
    "_auth",
)
# Case folding alone cannot recover word boundaries in APITOKEN/SESSIONKEY.
# Compare compact forms too, but do not treat a bare trailing "key" or "auth"
# as a credential (for example, the ordinary word "monkey").
_COMPACT_CREDENTIAL_KEYS = frozenset(key.replace("_", "") for key in _CREDENTIAL_KEYS)
_COMPACT_CREDENTIAL_SUFFIXES = tuple(
    suffix.replace("_", "")
    for suffix in _CREDENTIAL_SUFFIXES
    if suffix not in {"_key", "_key_id", "_auth"}
)
_RESERVATION_TERMINAL_STATUSES = {"completed", "failed"}


def _normalized_credential_key(key: object) -> str:
    text = str(key).strip()
    # Handle acronym prefixes (APIToken -> API_Token) as well as ordinary
    # camelCase (apiKey -> api_Key) before case-folding.
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", text)
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    return text.casefold().replace("-", "_").replace(" ", "_")


def _credential_like_key(key: object) -> bool:
    normalized = _normalized_credential_key(key)
    # Official OpenAI cache-routing option; it is not a secret credential.
    if normalized == "prompt_cache_key":
        return False
    compact = normalized.replace("_", "")
    return (
        normalized in _CREDENTIAL_KEYS
        or normalized.endswith(_CREDENTIAL_SUFFIXES)
        or compact in _COMPACT_CREDENTIAL_KEYS
        or compact.endswith(_COMPACT_CREDENTIAL_SUFFIXES)
    )


def _has_message_list(payload: object) -> bool:
    return isinstance(payload, dict) and isinstance(payload.get("messages"), list)


def reconstruct_replay_input(trace: Trace, span: Span) -> tuple[dict, str]:
    request_source = "span"
    if _has_message_list(span.input):
        replay_input = deepcopy(span.input)
    elif _has_message_list(trace.input):
        if sum(item.kind == "llm" for item in trace.spans) != 1:
            raise HTTPException(status_code=409, detail="历史输入对应多个 LLM Span，无法安全重建")
        replay_input = deepcopy(trace.input)
        request_source = "legacy_single_llm_trace"
    elif isinstance(span.input, dict) or isinstance(trace.input, dict):
        raise HTTPException(status_code=409, detail="历史请求的 messages 必须是列表")
    else:
        raise HTTPException(status_code=409, detail="该 Span 缺少可重放的历史请求")
    if any(_credential_like_key(key) for key in replay_input):
        raise HTTPException(status_code=409, detail="历史请求包含凭据字段，禁止重放")
    return replay_input, request_source


def request_fingerprint(trace_id: str, span_id: str, payload: ReplayRequest) -> str:
    canonical = json.dumps(
        {
            "source_trace_id": trace_id,
            "source_span_id": span_id,
            "confirm": True,
            "overrides": payload.overrides.model_dump(exclude_unset=True),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def current_requested_model(
    trace: Trace,
    span: Span,
    replay_input: dict,
    overrides: dict,
    settings,
):
    if "model" in overrides:
        return overrides["model"]
    span_metadata = span.metadata_json or {}
    if isinstance(span_metadata.get("requested_model"), str):
        return span_metadata["requested_model"]
    gateway = (trace.metadata_json or {}).get("gateway", {})
    if isinstance(gateway, dict) and isinstance(gateway.get("requested_model"), str):
        return gateway["requested_model"]
    if isinstance(replay_input.get("model"), str):
        return replay_input["model"]
    if isinstance(trace.model, str):
        return trace.model
    return settings.default_upstream_model


def output_message(output: object) -> dict:
    if not isinstance(output, dict):
        return {}
    try:
        message = output["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return {}
    return message if isinstance(message, dict) else {}


def comparison_source(source: Span | dict) -> dict:
    if isinstance(source, dict):
        content = source.get("content")
        reasoning = source.get("reasoning")
        output = source.get("output")
    else:
        content = source.content
        reasoning = source.reasoning
        output = source.output
    message = output_message(output)
    return {
        "content": content or "",
        "reasoning": reasoning or "",
        "tool_calls": message.get("tool_calls") or [],
    }


def build_comparison(source: dict, output: object, status: str) -> dict:
    message = output_message(output)
    original_content = source["content"]
    replay_content = message.get("content") or ""
    original_reasoning = source["reasoning"]
    replay_reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
    original_tools = source["tool_calls"]
    replay_tools = message.get("tool_calls") or []
    content_equal = original_content == replay_content
    reasoning_equal = original_reasoning == replay_reasoning
    tool_calls_equal = original_tools == replay_tools
    diff_lines = unified_diff(
        str(original_content).splitlines(keepends=True),
        str(replay_content).splitlines(keepends=True),
        fromfile="原始输出",
        tofile="重放输出",
    )
    content_diff = "".join(
        line if line.endswith("\n") else f"{line}\n" for line in diff_lines
    )
    return {
        "status": (
            "failed"
            if status == "failed"
            else "same"
            if content_equal and reasoning_equal and tool_calls_equal
            else "different"
        ),
        "content_equal": content_equal,
        "reasoning_equal": reasoning_equal,
        "tool_calls_equal": tool_calls_equal,
        "content_similarity": SequenceMatcher(
            None, str(original_content), str(replay_content)
        ).ratio(),
        "content_diff": content_diff,
        "attribution_note": ATTRIBUTION_NOTE,
    }


def replay_response(session, replay_trace_id: str, source_span_data: dict) -> dict:
    replay_trace = load_detail(session, replay_trace_id)
    if replay_trace is None:
        raise HTTPException(status_code=409, detail="重放审计记录不存在")
    replay_span = next((span for span in replay_trace.spans if span.kind == "llm"), None)
    if replay_span is None:
        raise HTTPException(status_code=409, detail="重放 LLM Span 不存在")
    lineage = (replay_trace.metadata_json or {}).get("replay", {})
    return {
        "replay_trace": trace_dict(replay_trace),
        "replay_span": span_dict(replay_span),
        "source_span": source_span_data,
        "comparison": deepcopy(lineage.get("comparison", {})),
    }


def validate_reservation(
    reservation: ReplayReservation,
    *,
    trace_id: str,
    span_id: str,
    fingerprint: str,
) -> None:
    """A retry may only reuse a server-created reservation for the same request.

    The reservation table is not writable through trace ingest, so this replaces
    the previous (forgeable) checks on Trace.metadata_json.
    """
    if (
        reservation.request_fingerprint != fingerprint
        or reservation.source_trace_id != trace_id
        or reservation.source_span_id != span_id
    ):
        raise HTTPException(status_code=409, detail="幂等键已被其他请求占用")
    if reservation.status == "running":
        raise HTTPException(status_code=409, detail="相同幂等键的重放仍在执行")
    if reservation.status not in _RESERVATION_TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="幂等键已被其他请求占用")
    if not isinstance(reservation.response_json, dict) or not reservation.response_json:
        raise HTTPException(status_code=409, detail="幂等键已被其他请求占用")


def store_reservation_snapshot(
    session_factory,
    replay_trace_id: str,
    *,
    status: str,
    response: dict,
    error: object = None,
) -> None:
    """Persist the exact response snapshot that later retries must replay."""
    with session_factory() as session:
        begin_write_transaction(session)
        reservation = session.get(ReplayReservation, replay_trace_id)
        if reservation is None:
            raise RuntimeError("重放预留记录不存在")
        reservation.status = status
        reservation.response_json = deepcopy(response)
        reservation.error = deepcopy(error)
        reservation.updated_at = datetime.now(UTC)
        session.commit()


def mark_reservation_failed(session_factory, replay_trace_id: str, error: dict) -> None:
    """Best-effort terminal failed reservation when no response snapshot exists."""
    with session_factory() as session:
        begin_write_transaction(session)
        reservation = session.get(ReplayReservation, replay_trace_id)
        if reservation is None:
            return
        reservation.status = "failed"
        reservation.error = deepcopy(error)
        reservation.updated_at = datetime.now(UTC)
        session.commit()


def _utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


async def attempt_candidate(candidate, replay_input: dict, *, stream: bool) -> dict:
    attempt_payload = deepcopy(replay_input)
    if candidate.target_model:
        attempt_payload["model"] = candidate.target_model
    headers = {"Content-Type": "application/json"}
    if candidate.api_key:
        headers["Authorization"] = f"Bearer {candidate.api_key}"
    client = httpx.AsyncClient(timeout=60)
    upstream = None
    output = None
    error = None
    stream_started = False
    try:
        url = f"{candidate.upstream_base.rstrip('/')}/chat/completions"
        body = json.dumps(attempt_payload, ensure_ascii=False).encode("utf-8")
        if stream:
            upstream = await client.send(
                client.build_request("POST", url, content=body, headers=headers),
                stream=True,
            )
            if upstream.is_success:
                stream_started = True
                accumulator = SSEAccumulator()
                try:
                    async for chunk in upstream.aiter_raw():
                        accumulator.feed(chunk)
                    accumulator.finish()
                except httpx.HTTPError as exc:
                    error = {"type": type(exc).__name__, "message": str(exc)}
                output = accumulator.output(attempt_payload)
                if accumulator.error is not None:
                    error = sse_error_evidence(accumulator.error)
                elif error is None and not accumulator.done:
                    error = {
                        "type": "IncompleteStream",
                        "message": "上游 SSE 流未以 [DONE] 正常结束",
                    }
            else:
                output = response_json(await upstream.aread())
                error = {"status_code": upstream.status_code, "body": output}
        else:
            upstream = await client.post(url, content=body, headers=headers)
            output = response_json(upstream.content)
            if not upstream.is_success:
                error = {"status_code": upstream.status_code, "body": output}
    except httpx.HTTPError as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
        output = None
    except BaseException:
        await close_attempt_resources(upstream, client, suppress_errors=True)
        raise
    status_code = upstream.status_code if upstream is not None else None
    await close_attempt_resources(upstream, client)
    return {
        "candidate": candidate,
        "payload": attempt_payload,
        "output": output,
        "error": error,
        "status_code": status_code,
        "stream_started": stream_started,
        "success": error is None,
    }


def persist_failed_reservation(
    session_factory,
    replay_trace_id: str,
    *,
    trace_id: str,
    replay_input: dict,
    requested_model: str,
    lineage: dict,
    comparison: dict,
    error: dict,
    started_at: datetime,
) -> None:
    """Best-effort terminal failed record so a stuck reservation can be retried."""
    ended_at = datetime.now(UTC)
    with session_factory() as session:
        begin_write_transaction(session)
        trace = session.get(Trace, replay_trace_id)
        if trace is None:
            trace = Trace(
                id=replay_trace_id,
                parent_id=trace_id,
                name="LLM 单 Span 重放",
                source="replay",
                status="running",
                model=requested_model,
                input=deepcopy(replay_input),
                started_at=started_at,
                tags=["replay"],
                metadata_json={"replay": deepcopy(lineage)},
            )
            session.add(trace)
            session.flush()
        if trace.status != "running":
            return
        trace.status = "failed"
        trace.model = requested_model
        trace.input = deepcopy(replay_input)
        trace.output = None
        trace.error = deepcopy(error)
        trace.tags = ["replay"]
        trace.started_at = _utc(trace.started_at) or started_at
        trace.ended_at = ended_at
        trace.duration_ms = (ended_at - trace.started_at).total_seconds() * 1000
        metadata = deepcopy(trace.metadata_json or {})
        metadata["replay"] = {**deepcopy(lineage), "comparison": deepcopy(comparison)}
        trace.metadata_json = metadata
        if not any(span.kind == "llm" for span in trace.spans):
            trace.spans.append(
                Span(
                    id=f"{replay_trace_id}-failed-llm",
                    trace_id=replay_trace_id,
                    sequence=0,
                    kind="llm",
                    name="重放模型响应",
                    status="failed",
                    started_at=started_at,
                    ended_at=ended_at,
                    input=deepcopy(replay_input),
                    output=None,
                    error=deepcopy(error),
                    metadata_json={},
                )
            )
        session.commit()


@router.post("/traces/{trace_id}/spans/{span_id}/replay", status_code=201)
async def replay_span(
    trace_id: str,
    span_id: str,
    payload: ReplayRequest,
    request: Request,
    idempotency_key: UUID = Header(alias="Idempotency-Key"),
):
    settings = request.app.state.settings
    session_factory = request.app.state.session_factory
    circuits = request.app.state.circuits
    overrides = payload.overrides.model_dump(exclude_unset=True)
    fingerprint = request_fingerprint(trace_id, span_id, payload)
    replay_trace_id = str(idempotency_key)

    with session_factory() as session:
        reservation = session.get(ReplayReservation, replay_trace_id)
        if reservation is not None:
            validate_reservation(
                reservation,
                trace_id=trace_id,
                span_id=span_id,
                fingerprint=fingerprint,
            )
            return deepcopy(reservation.response_json)

        # No server reservation exists. A Trace already occupying this UUID was
        # created by arbitrary ingest, not by a genuine replay: reject without
        # loading the source, contacting upstream, or reflecting its contents.
        if session.get(Trace, replay_trace_id) is not None:
            raise HTTPException(status_code=409, detail="幂等键已被其他请求占用")

        trace = session.get(Trace, trace_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="审计链路不存在")
        span = session.get(Span, span_id)
        if span is None or span.trace_id != trace_id:
            raise HTTPException(status_code=404, detail="该 Span 不属于当前链路")
        if span.kind != "llm":
            raise HTTPException(status_code=409, detail="只能重放 LLM Span")
        replay_input, request_source = reconstruct_replay_input(trace, span)
        requested_model = current_requested_model(
            trace, span, replay_input, overrides, settings
        )
        candidates = load_candidates(
            session, settings, request.app.state.env_path, requested_model
        )
        source_span_data = span_dict(span)
        source_comparison = comparison_source(source_span_data)

        if all(circuits.is_open(candidate.circuit_key) for candidate in candidates):
            raise HTTPException(
                status_code=503, detail="所有匹配路由均处于熔断冷却期，请稍后再试"
            )

    budget = settings.gateway_daily_token_budget
    if budget and daily_token_usage(session_factory) >= budget:
        raise HTTPException(status_code=429, detail="每日 Token 预算已用尽，请调整预算或明日再试")

    replay_input.update(overrides)
    started_at = datetime.now(UTC)
    lineage = {
        "source_trace_id": trace_id,
        "source_span_id": span_id,
        "request_source": request_source,
        "overrides": overrides,
        "mode": "llm_span",
        "idempotency_key": replay_trace_id,
        "request_fingerprint": fingerprint,
    }

    def reserve_run() -> None:
        with session_factory() as session:
            begin_write_transaction(session)
            reservation = ReplayReservation(
                idempotency_key=replay_trace_id,
                request_fingerprint=fingerprint,
                source_trace_id=trace_id,
                source_span_id=span_id,
                status="running",
            )
            run = Trace(
                id=replay_trace_id,
                parent_id=trace_id,
                name="LLM 单 Span 重放",
                source="replay",
                status="running",
                model=requested_model,
                input=replay_input,
                started_at=started_at,
                tags=["replay"],
                metadata_json={"replay": lineage},
            )
            session.add_all([reservation, run])
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise HTTPException(status_code=409, detail="幂等键已被其他请求占用") from None

    # A contended SQLite write lock must not block the async event loop.
    await run_db(reserve_run)

    try:
        stream = bool(replay_input.get("stream"))
        attempts: list[dict] = []
        final = None
        for index, candidate in enumerate(candidates):
            info = attempt_info(candidate)
            if circuits.is_open(candidate.circuit_key):
                attempts.append({**info, "outcome": "circuit_open"})
                continue
            result = await attempt_candidate(candidate, replay_input, stream=stream)
            status_code = result["status_code"]
            if result["success"]:
                circuits.record_success(candidate.circuit_key)
                attempts.append(
                    {**info, "outcome": "success", "status_code": status_code}
                )
                final = result
                break
            retryable = status_code is None or retryable_upstream(status_code)
            if retryable or result["stream_started"]:
                circuits.record_failure(
                    candidate.circuit_key,
                    settings.gateway_failure_threshold,
                    settings.gateway_cooldown_seconds,
                )
            # Once an SSE stream produced bytes it cannot be replayed transparently.
            retry = (
                index + 1 < len(candidates)
                and retryable
                and not result["stream_started"]
            )
            entry = {
                **info,
                "outcome": "retry" if retry else "error",
                "status_code": status_code,
            }
            error = result["error"]
            if isinstance(error, dict) and isinstance(error.get("type"), str):
                entry["error_type"] = error["type"]
            attempts.append(entry)
            final = result
            if not retry:
                break
        if final is None:
            raise HTTPException(
                status_code=503, detail="所有匹配路由均处于熔断冷却期，请稍后再试"
            )

        status = "completed" if final["success"] else "failed"
        output = final["output"]
        gateway = gateway_metadata(
            final["candidate"], settings, requested_model, attempts, output
        )
        comparison = build_comparison(source_comparison, output, status)
        await run_db(
            persist_openai_trace,
            session_factory,
            replay_trace_id,
            final["payload"],
            output,
            status,
            started_at,
            datetime.now(UTC),
            error=final["error"],
            gateway=gateway,
            replay={
                "parent_id": trace_id,
                "source": "replay",
                "name": "LLM 单 Span 重放",
                "tags": ["replay"],
                "metadata": {"replay": {"comparison": comparison}},
            },
            upstream_status=final["status_code"],
        )
        with session_factory() as session:
            response = replay_response(session, replay_trace_id, source_span_data)
        # The snapshot is the only payload a retry may ever receive. Persisting it
        # after the trace commit keeps the response tied to durable server state.
        await run_db(
            store_reservation_snapshot,
            session_factory,
            replay_trace_id,
            status=status,
            response=response,
            error=final["error"],
        )
        return deepcopy(response)
    except BaseException as exc:
        # An unexpected dispatch/persistence failure or cancellation must not leave
        # the idempotency reservation running forever. The cleanup is shielded and
        # best-effort; the original failure is always re-raised.
        with anyio.CancelScope(shield=True):
            await anyio.lowlevel.checkpoint()
            failure = {
                "type": type(exc).__name__,
                "message": str(exc) or "重放执行被中断",
            }
            with suppress(Exception):
                await run_db(
                    persist_failed_reservation,
                    session_factory,
                    replay_trace_id,
                    trace_id=trace_id,
                    replay_input=replay_input,
                    requested_model=requested_model,
                    lineage=lineage,
                    comparison=build_comparison(source_comparison, None, "failed"),
                    error=failure,
                    started_at=started_at,
                )
            snapshot_stored = False
            with suppress(Exception):
                with session_factory() as session:
                    failed_trace = load_detail(session, replay_trace_id)
                    failed_response = (
                        replay_response(session, replay_trace_id, source_span_data)
                        if failed_trace is not None
                        and failed_trace.status in {"failed", "error"}
                        else None
                    )
                if failed_response is not None:
                    await run_db(
                        store_reservation_snapshot,
                        session_factory,
                        replay_trace_id,
                        status="failed",
                        response=failed_response,
                        error=failure,
                    )
                    snapshot_stored = True
            if not snapshot_stored:
                # A terminal reservation without a snapshot correctly answers 409;
                # never fabricate a successful response after a failed commit.
                with suppress(Exception):
                    await run_db(mark_reservation_failed, session_factory, replay_trace_id, failure)
        raise
