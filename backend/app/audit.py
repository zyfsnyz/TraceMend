from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .models import Span, Trace
from .rules import run_rules
from .schemas import IngestIn, SpanIn, TraceIn

TRACE_FIELDS = {
    "parent_id",
    "name",
    "source",
    "status",
    "model",
    "input",
    "output",
    "started_at",
    "ended_at",
    "duration_ms",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "tags",
    "error",
}
SPAN_FIELDS = {
    "parent_span_id",
    "sequence",
    "kind",
    "name",
    "status",
    "started_at",
    "ended_at",
    "duration_ms",
    "input",
    "output",
    "content",
    "reasoning",
    "tool_name",
    "tool_arguments",
    "tool_result",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "error",
}


def duration_ms(started_at, ended_at, explicit: float | None) -> float | None:
    if explicit is not None:
        return explicit
    if started_at is not None and ended_at is not None:
        return (ended_at - started_at).total_seconds() * 1000
    return None


def apply_trace(trace: Trace, data: TraceIn) -> None:
    for field in TRACE_FIELDS:
        setattr(trace, field, getattr(data, field))
    trace.metadata_json = data.metadata
    trace.duration_ms = duration_ms(data.started_at, data.ended_at, data.duration_ms)


def make_span(data: SpanIn, trace_id: str) -> Span:
    values = {field: getattr(data, field) for field in SPAN_FIELDS}
    values["duration_ms"] = duration_ms(data.started_at, data.ended_at, data.duration_ms)
    return Span(id=data.id, trace_id=trace_id, metadata_json=data.metadata, **values)


def ingest(session: Session, payload: IngestIn) -> tuple[Trace, int]:
    trace = session.get(Trace, payload.trace.id)
    if trace is None:
        trace = Trace(id=payload.trace.id)
        session.add(trace)
    apply_trace(trace, payload.trace)
    if trace.spans:
        for span in list(trace.spans):
            session.delete(span)
        session.flush()
    trace.spans = [
        make_span(span, trace.id) for span in sorted(payload.spans, key=lambda item: item.sequence)
    ]
    session.flush()
    finding_count = run_rules(session, trace)
    session.commit()
    return trace, finding_count


def load_detail(session: Session, trace_id: str) -> Trace | None:
    return session.scalar(
        select(Trace)
        .where(Trace.id == trace_id)
        .options(
            selectinload(Trace.spans), selectinload(Trace.findings), selectinload(Trace.comments)
        )
    )
