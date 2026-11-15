from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from .models import Span, Trace


def daily_token_usage(session_factory) -> int:
    """Sum today's individual proxy LLM spans, not the parent trace's first start time."""
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today + timedelta(days=1)
    occurred_at = func.coalesce(Span.started_at, Trace.created_at)
    with session_factory() as session:
        return int(
            session.scalar(
                select(func.coalesce(func.sum(Span.total_tokens), 0))
                .join(Trace, Span.trace_id == Trace.id)
                .where(
                    Trace.source == "openai-proxy",
                    Span.kind == "llm",
                    occurred_at >= today,
                    occurred_at < tomorrow,
                )
            )
            or 0
        )
