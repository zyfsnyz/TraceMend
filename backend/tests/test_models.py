from sqlalchemy import func, select

from app.db import Base, make_engine, make_session_factory
from app.models import Comment, Finding, Span, Trace


def test_trace_delete_cascades_all_audit_records(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'models.db'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)

    with factory() as session:
        trace = Trace(id="trace-1", name="测试", source="test", status="completed")
        span = Span(id="span-1", trace=trace, sequence=0, kind="llm", name="模型")
        session.add_all(
            [
                trace,
                span,
                Finding(
                    id="finding-1",
                    trace=trace,
                    span=span,
                    source="rule",
                    rule_code="tool_error",
                    severity="high",
                    category="工具",
                    title="工具异常",
                    explanation="失败",
                ),
                Comment(
                    id="comment-1",
                    trace=trace,
                    span=span,
                    author="审计员",
                    body="复核",
                    status="open",
                ),
            ]
        )
        session.commit()
        session.delete(trace)
        session.commit()

        for model in (Trace, Span, Finding, Comment):
            assert session.scalar(select(func.count()).select_from(model)) == 0
