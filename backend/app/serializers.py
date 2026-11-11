from datetime import datetime
from typing import Any

from .models import Comment, Finding, Span, Trace


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def trace_dict(
    trace: Trace, span_count: int | None = None, risk_count: int | None = None
) -> dict[str, Any]:
    return {
        "id": trace.id,
        "parent_id": trace.parent_id,
        "name": trace.name,
        "source": trace.source,
        "status": trace.status,
        "model": trace.model,
        "input": trace.input,
        "output": trace.output,
        "started_at": iso(trace.started_at),
        "ended_at": iso(trace.ended_at),
        "duration_ms": trace.duration_ms,
        "prompt_tokens": trace.prompt_tokens,
        "completion_tokens": trace.completion_tokens,
        "total_tokens": trace.total_tokens,
        "tags": trace.tags or [],
        "metadata": trace.metadata_json or {},
        "error": trace.error,
        "created_at": iso(trace.created_at),
        "span_count": len(trace.spans) if span_count is None else span_count,
        "risk_count": len(trace.findings) if risk_count is None else risk_count,
    }


def span_dict(span: Span) -> dict[str, Any]:
    return {
        "id": span.id,
        "trace_id": span.trace_id,
        "parent_span_id": span.parent_span_id,
        "sequence": span.sequence,
        "kind": span.kind,
        "name": span.name,
        "status": span.status,
        "started_at": iso(span.started_at),
        "ended_at": iso(span.ended_at),
        "duration_ms": span.duration_ms,
        "input": span.input,
        "output": span.output,
        "content": span.content,
        "reasoning": span.reasoning,
        "tool_name": span.tool_name,
        "tool_arguments": span.tool_arguments,
        "tool_result": span.tool_result,
        "prompt_tokens": span.prompt_tokens,
        "completion_tokens": span.completion_tokens,
        "total_tokens": span.total_tokens,
        "error": span.error,
        "metadata": span.metadata_json or {},
    }


def finding_dict(finding: Finding) -> dict[str, Any]:
    return {
        "id": finding.id,
        "trace_id": finding.trace_id,
        "span_id": finding.span_id,
        "source": finding.source,
        "rule_code": finding.rule_code,
        "severity": finding.severity,
        "category": finding.category,
        "title": finding.title,
        "explanation": finding.explanation,
        "evidence": finding.evidence,
        "created_at": iso(finding.created_at),
    }


def comment_dict(comment: Comment) -> dict[str, Any]:
    return {
        "id": comment.id,
        "trace_id": comment.trace_id,
        "span_id": comment.span_id,
        "author": comment.author,
        "body": comment.body,
        "status": comment.status,
        "created_at": iso(comment.created_at),
        "updated_at": iso(comment.updated_at),
    }
