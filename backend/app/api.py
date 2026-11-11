import math
from collections import Counter
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import selectinload

from .audit import ingest, load_detail
from .models import Finding, Trace
from .rules import run_rules
from .schemas import IngestIn
from .serializers import comment_dict, finding_dict, span_dict, trace_dict

router = APIRouter(prefix="/api")


@router.post("/traces/ingest")
def ingest_trace(payload: IngestIn, request: Request):
    with request.app.state.session_factory() as session:
        trace, finding_count = ingest(session, payload)
        return {
            "trace_id": trace.id,
            "span_count": len(payload.spans),
            "finding_count": finding_count,
        }


@router.get("/traces")
def list_traces(
    request: Request,
    q: str | None = None,
    status: str | None = None,
    risk: str | None = None,
    tag: str | None = None,
    from_time: datetime | None = Query(default=None, alias="from"),
    to_time: datetime | None = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
):
    filters = []
    if q:
        pattern = f"%{q}%"
        filters.append(
            or_(
                Trace.name.like(pattern),
                Trace.source.like(pattern),
                Trace.model.like(pattern),
                cast(Trace.input, String).like(pattern),
                cast(Trace.output, String).like(pattern),
            )
        )
    if status:
        filters.append(Trace.status == status)
    if risk:
        finding_query = select(Finding.id).where(Finding.trace_id == Trace.id)
        if risk == "none":
            filters.append(~finding_query.exists())
        elif risk == "any":
            filters.append(finding_query.exists())
        else:
            filters.append(finding_query.where(Finding.severity == risk).exists())
    if tag:
        filters.append(cast(Trace.tags, String).like(f'%"{tag}"%'))
    if from_time:
        filters.append(Trace.started_at >= from_time)
    if to_time:
        filters.append(Trace.started_at <= to_time)

    with request.app.state.session_factory() as session:
        total = session.scalar(select(func.count(Trace.id)).where(*filters)) or 0
        traces = session.scalars(
            select(Trace)
            .where(*filters)
            .options(selectinload(Trace.spans), selectinload(Trace.findings))
            .order_by(Trace.created_at.desc(), Trace.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return {
            "items": [trace_dict(trace) for trace in traces],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": math.ceil(total / page_size) if total else 0,
        }


@router.get("/traces/{trace_id}")
def get_trace(trace_id: str, request: Request):
    with request.app.state.session_factory() as session:
        trace = load_detail(session, trace_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="审计链路不存在")
        return {
            "trace": trace_dict(trace),
            "spans": [
                span_dict(span) for span in sorted(trace.spans, key=lambda item: item.sequence)
            ],
            "findings": [finding_dict(finding) for finding in trace.findings],
            "comments": [comment_dict(comment) for comment in trace.comments],
        }


@router.post("/traces/{trace_id}/reanalyze")
def reanalyze_trace(trace_id: str, request: Request):
    with request.app.state.session_factory() as session:
        trace = load_detail(session, trace_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="审计链路不存在")
        finding_count = run_rules(session, trace)
        session.commit()
        return {"trace_id": trace_id, "finding_count": finding_count}


@router.get("/stats")
def get_stats(request: Request, days: int = Query(default=30, ge=1, le=3650)):
    cutoff = datetime.now(UTC) - timedelta(days=days)
    with request.app.state.session_factory() as session:
        traces = session.scalars(
            select(Trace)
            .where(func.coalesce(Trace.started_at, Trace.created_at) >= cutoff)
            .options(selectinload(Trace.spans), selectinload(Trace.findings))
        ).all()
        durations = [trace.duration_ms for trace in traces if trace.duration_ms is not None]
        daily = Counter(
            (trace.started_at or trace.created_at).date().isoformat() for trace in traces
        )
        models = Counter(trace.model or "未知模型" for trace in traces)
        return {
            "total_traces": len(traces),
            "completed_traces": sum(trace.status == "completed" for trace in traces),
            "error_traces": sum(trace.status in {"error", "failed"} for trace in traces),
            "risk_traces": sum(bool(trace.findings) for trace in traces),
            "total_findings": sum(len(trace.findings) for trace in traces),
            "total_tokens": sum(trace.total_tokens or 0 for trace in traces),
            "avg_duration_ms": sum(durations) / len(durations) if durations else 0,
            "tool_calls": sum(span.kind == "tool" for trace in traces for span in trace.spans),
            "models": dict(models),
            "daily": [{"date": date, "count": count} for date, count in sorted(daily.items())],
        }


@router.delete("/traces/{trace_id}", status_code=204)
def delete_trace(trace_id: str, request: Request):
    with request.app.state.session_factory() as session:
        trace = session.get(Trace, trace_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="审计链路不存在")
        session.delete(trace)
        session.commit()
    return Response(status_code=204)
