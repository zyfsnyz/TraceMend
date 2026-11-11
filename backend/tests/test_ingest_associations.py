import pytest
from factories import trace_payload, updated_payload
from sqlalchemy import select

from app.db import make_engine, make_session_factory
from app.models import Comment, Finding, Span, Trace


def add_associations(tmp_path):
    factory = make_session_factory(make_engine(f"sqlite:///{tmp_path / 'test.db'}"))
    with factory() as session:
        session.add_all(
            [
                Comment(
                    id="user-comment",
                    trace_id="trace-1",
                    span_id="span-user",
                    author="审计员",
                    body="保留用户评论",
                ),
                Comment(
                    id="llm-comment",
                    trace_id="trace-1",
                    span_id="span-llm",
                    author="审计员",
                    body="随模型 span 删除",
                ),
                Finding(
                    id="llm-review",
                    trace_id="trace-1",
                    span_id="span-llm",
                    source="llm",
                    rule_code="manual_review",
                    severity="low",
                    category="复核",
                    title="二阶复核",
                    explanation="真正删除 span 后保留 Trace 级 Finding",
                ),
            ]
        )
        session.commit()
    return factory


@pytest.mark.asyncio
async def test_ingest_diff_preserves_associations_and_db_delete_conventions(client, tmp_path):
    payload = trace_payload()
    assert (await client.post("/api/traces/ingest", json=payload)).status_code == 200
    factory = add_associations(tmp_path)

    assert (await client.post("/api/traces/ingest", json=payload)).status_code == 200
    detail = (await client.get("/api/traces/trace-1")).json()
    assert {comment["id"]: comment["span_id"] for comment in detail["comments"]} == {
        "user-comment": "span-user",
        "llm-comment": "span-llm",
    }
    assert detail["findings"][0]["id"] == "llm-review"
    assert detail["findings"][0]["span_id"] == "span-llm"

    assert (await client.post("/api/traces/ingest", json=updated_payload())).status_code == 200
    detail = (await client.get("/api/traces/trace-1")).json()
    assert [(comment["id"], comment["span_id"]) for comment in detail["comments"]] == [
        ("user-comment", "span-user")
    ]
    assert [(finding["id"], finding["span_id"]) for finding in detail["findings"]] == [
        ("llm-review", None)
    ]

    with factory() as session:
        assert session.get(Comment, "llm-comment") is None
        assert session.get(Finding, "llm-review").span_id is None


def test_direct_span_delete_relies_on_database_cascade_and_set_null(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'delete-span.db'}")
    from app.db import Base

    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        trace = Trace(id="trace-1", name="测试", source="test")
        session.add(trace)
        session.add(Span(id="span-llm", trace=trace, sequence=1, kind="llm"))
        session.flush()
        session.add_all(
            [
                Comment(
                    id="comment",
                    trace_id="trace-1",
                    span_id="span-llm",
                    author="审计员",
                    body="删除",
                ),
                Finding(
                    id="finding",
                    trace_id="trace-1",
                    span_id="span-llm",
                    source="llm",
                    rule_code="review",
                    severity="low",
                    category="复核",
                    title="复核",
                    explanation="保留",
                ),
            ]
        )
        session.commit()

    with factory() as session:
        span = session.scalar(select(Span).where(Span.id == "span-llm"))
        session.delete(span)
        session.commit()
        assert session.get(Comment, "comment") is None
        assert session.get(Finding, "finding").span_id is None
