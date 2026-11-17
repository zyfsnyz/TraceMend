from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from .models import Span, Trace

# Replay LLM calls spend the same upstream budget as proxied calls, so a fresh
# idempotency key must not be able to bypass the daily cap.
BUDGETED_TRACE_SOURCES = ("openai-proxy", "replay")


def daily_token_usage(session_factory) -> int:
    """Sum today's individual budgeted LLM spans, not a parent trace's first start."""
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today + timedelta(days=1)
    occurred_at = func.coalesce(Span.started_at, Trace.created_at)
    with session_factory() as session:
        return int(
            session.scalar(
                select(func.coalesce(func.sum(Span.total_tokens), 0))
                .join(Trace, Span.trace_id == Trace.id)
                .where(
                    Trace.source.in_(BUDGETED_TRACE_SOURCES),
                    Span.kind == "llm",
                    occurred_at >= today,
                    occurred_at < tomorrow,
                )
            )
            or 0
        )
