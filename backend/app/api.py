import hashlib
import math
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import selectinload

from .audit import ingest, load_detail
from .llm_check import LLMCheckError, run_llm_check
from .models import Comment, Finding, GatewayRoute, Span, Trace
from .reports import render_markdown, render_pdf
from .rules import run_rules
from .schemas import CommentCreate, CommentPatch, IngestIn, TraceCommentIn
from .serializers import comment_dict, finding_dict, span_dict, trace_dict

router = APIRouter(prefix="/api")


def report_content_disposition(trace_id: str, extension: str) -> str:
    """Build an ASCII-only attachment name from an untrusted trace id."""
    digest = hashlib.sha256(trace_id.encode("utf-8")).hexdigest()[:8]
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", trace_id).strip("-")[:48]
    stem = f"{slug}-{digest}" if slug else digest
    return f'attachment; filename="agenttrace-{stem}.{extension}"'


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


@router.post("/traces/{trace_id}/comments", status_code=201)
def create_trace_comment(trace_id: str, payload: TraceCommentIn, request: Request):
    with request.app.state.session_factory() as session:
        trace = load_detail(session, trace_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="审计链路不存在")
        if payload.span_id is not None and not any(
            span.id == payload.span_id for span in trace.spans
        ):
            raise HTTPException(status_code=404, detail="该 Span 不属于当前链路")
        comment = Comment(
            id=str(uuid4()),
            trace_id=trace_id,
            span_id=payload.span_id,
            author=payload.author,
            body=payload.body,
            status=payload.status,
        )
        session.add(comment)
        session.commit()
        session.refresh(comment)
        return comment_dict(comment)


@router.post("/traces/{trace_id}/spans/{span_id}/comments", status_code=201)
def create_span_comment(
    trace_id: str, span_id: str, payload: CommentCreate, request: Request
):
    with request.app.state.session_factory() as session:
        trace = session.get(Trace, trace_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="审计链路不存在")
        span = session.get(Span, span_id)
        if span is None or span.trace_id != trace_id:
            raise HTTPException(status_code=404, detail="该 Span 不属于当前链路")
        comment = Comment(
            id=str(uuid4()),
            trace_id=trace_id,
            span_id=span_id,
            author=payload.author,
            body=payload.body,
            status=payload.status,
        )
        session.add(comment)
        session.commit()
        session.refresh(comment)
        return comment_dict(comment)


@router.patch("/comments/{comment_id}")
def update_comment(comment_id: str, payload: CommentPatch, request: Request):
    with request.app.state.session_factory() as session:
        comment = session.get(Comment, comment_id)
        if comment is None:
            raise HTTPException(status_code=404, detail="审计评论不存在")
        for field in ("author", "body", "status"):
            value = getattr(payload, field)
            if value is not None:
                setattr(comment, field, value)
        session.commit()
        session.refresh(comment)
        return comment_dict(comment)


@router.get("/traces/{trace_id}/report.md")
def export_markdown_report(trace_id: str, request: Request):
    with request.app.state.session_factory() as session:
        trace = load_detail(session, trace_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="审计链路不存在")
        content = render_markdown(trace)
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": report_content_disposition(trace_id, "md")},
    )


@router.get("/traces/{trace_id}/report.pdf")
def export_pdf_report(trace_id: str, request: Request):
    with request.app.state.session_factory() as session:
        trace = load_detail(session, trace_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="审计链路不存在")
        content = render_pdf(trace)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": report_content_disposition(trace_id, "pdf")},
    )


@router.post("/traces/{trace_id}/llm-check")
async def llm_check_trace(trace_id: str, request: Request):
    settings = request.app.state.settings
    if not settings.enable_llm_check:
        raise HTTPException(status_code=409, detail="二阶 LLM 检测未启用")
    if not settings.check_model_key:
        raise HTTPException(status_code=409, detail="二阶 LLM 检测未配置模型密钥")
    with request.app.state.session_factory() as session:
        trace = load_detail(session, trace_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="审计链路不存在")
        try:
            findings = await run_llm_check(trace, settings)
        except LLMCheckError as exc:
            trace.metadata_json = {
                **(trace.metadata_json or {}),
                "llm_check": {
                    "status": "failed",
                    "model": settings.check_model_name,
                    "checked_at": datetime.now(UTC).isoformat(),
                    "error": str(exc),
                },
            }
            session.commit()
            raise HTTPException(
                status_code=502, detail="检测模型调用失败，请检查模型地址、密钥和网络"
            ) from exc
        for finding in list(trace.findings):
            if finding.source == "llm":
                session.delete(finding)
        session.flush()
        for item in findings:
            trace.findings.append(Finding(id=str(uuid4()), trace_id=trace.id, source="llm", **item))
        trace.metadata_json = {
            **(trace.metadata_json or {}),
            "llm_check": {
                "status": "completed",
                "model": settings.check_model_name,
                "checked_at": datetime.now(UTC).isoformat(),
                "findings": len(findings),
            },
        }
        session.commit()
        return {
            "trace_id": trace.id,
            "finding_count": len(trace.findings),
            "llm_finding_count": len(findings),
        }


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
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        today_tokens = int(
            session.scalar(
                select(func.sum(Trace.total_tokens)).where(
                    Trace.source == "openai-proxy",
                    func.coalesce(Trace.started_at, Trace.created_at) >= today,
                )
            )
            or 0
        )
        costs = []
        for trace in traces:
            gateway = (trace.metadata_json or {}).get("gateway", {})
            cost = gateway.get("cost_usd", 0) if isinstance(gateway, dict) else 0
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                costs.append(cost)
        routes = session.scalars(select(GatewayRoute)).all()
        open_circuits = sum(
            request.app.state.circuits.is_open(route.id) for route in routes
        )
        return {
            "total_traces": len(traces),
            "completed_traces": sum(trace.status == "completed" for trace in traces),
            "error_traces": sum(trace.status in {"error", "failed"} for trace in traces),
            "risk_traces": sum(bool(trace.findings) for trace in traces),
            "total_findings": sum(len(trace.findings) for trace in traces),
            "total_tokens": sum(trace.total_tokens or 0 for trace in traces),
            "total_cost_usd": round(sum(costs), 10),
            "today_tokens": today_tokens,
            "daily_token_budget": request.app.state.settings.gateway_daily_token_budget,
            "active_routes": sum(route.enabled for route in routes),
            "open_circuits": open_circuits,
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
