import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from .models import Finding, Trace

URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
NUMBER_RE = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?")


def normalized_number(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", ""))
    except InvalidOperation:
        return None


def evidence_values(value: Any):
    if isinstance(value, dict):
        for item in value.values():
            yield from evidence_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from evidence_values(item)
    elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
        yield value


def explicit_fields(value: Any, prefix=""):
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(item, dict):
                yield from explicit_fields(item, path)
            else:
                yield path, item


def final_answer(trace: Trace) -> tuple[str, object | None]:
    final_span = next(
        (span for span in reversed(trace.spans) if span.kind == "llm" and span.content),
        None,
    )
    output = trace.output
    text = ""
    if isinstance(output, str):
        text = output
    elif isinstance(output, dict):
        text = str(output.get("content") or output.get("text") or "")
        if not text and output.get("choices"):
            try:
                text = output["choices"][0]["message"].get("content") or ""
            except (KeyError, IndexError, TypeError, AttributeError):
                text = ""
    if not text and final_span is not None:
        text = final_span.content or ""
    return text, final_span


def contains_citation(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in {"source", "citation", "citations", "url"} and item:
                return True
            if contains_citation(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(contains_citation(item) for item in value)
    elif isinstance(value, str) and URL_RE.search(value):
        return True
    return False


def run_rules(session: Session, trace: Trace) -> int:
    for finding in list(trace.findings):
        if finding.source == "rule":
            session.delete(finding)
    session.flush()

    findings: list[Finding] = []
    for span in trace.spans:
        upstream_status = (span.metadata_json or {}).get("upstream_status")
        failed = (
            span.status in {"error", "failed"}
            or span.error is not None
            or (isinstance(upstream_status, int) and upstream_status >= 400)
        )
        if span.kind in {"tool", "tool_result"} and failed:
            findings.append(
                Finding(
                    id=str(uuid4()),
                    trace_id=trace.id,
                    span_id=span.id,
                    source="rule",
                    rule_code="tool_error",
                    severity="high",
                    category="工具可靠性",
                    title="工具调用异常",
                    explanation=(
                        f"工具 {span.tool_name or span.name or '未命名'} 返回错误或失败状态。"
                    ),
                    evidence={
                        "span_id": span.id,
                        "status": span.status,
                        "error": span.error,
                        "upstream_status": upstream_status,
                    },
                )
            )
        result = span.tool_result if span.tool_result is not None else span.output
        is_empty = result is None or result == "" or result == [] or result == {}
        if span.kind == "tool_result" and not failed and is_empty:
            findings.append(
                Finding(
                    id=str(uuid4()),
                    trace_id=trace.id,
                    span_id=span.id,
                    source="rule",
                    rule_code="empty_tool_result",
                    severity="medium",
                    category="工具可靠性",
                    title="工具返回空结果",
                    explanation=(
                        f"工具 {span.tool_name or span.name or '未命名'} "
                        "没有返回可供后续推理使用的证据。"
                    ),
                    evidence={"span_id": span.id, "tool_result": result},
                )
            )
        if span.kind == "tool":
            arguments = span.tool_arguments
            parse_error = None
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except (json.JSONDecodeError, TypeError) as exc:
                    parse_error = str(exc)
            schema = (span.metadata_json or {}).get("tool_schema") or {}
            required = schema.get("required", []) if isinstance(schema, dict) else []
            missing = [
                field
                for field in required
                if not isinstance(arguments, dict) or field not in arguments
            ]
            if parse_error or missing:
                findings.append(
                    Finding(
                        id=str(uuid4()),
                        trace_id=trace.id,
                        span_id=span.id,
                        source="rule",
                        rule_code="invalid_arguments",
                        severity="high",
                        category="参数质量",
                        title="工具参数无效",
                        explanation=(
                            "工具参数不是有效的 JSON。"
                            if parse_error
                            else f"工具参数缺少必填字段：{', '.join(missing)}。"
                        ),
                        evidence={
                            "span_id": span.id,
                            "reason": "invalid_json" if parse_error else "missing_required",
                            "missing_fields": missing,
                            "tool_arguments": span.tool_arguments,
                        },
                    )
                )
    answer, answer_span = final_answer(trace)
    if re.search(r"根据|来源|研究表明", answer):
        has_citation = contains_citation(trace.input) or any(
            contains_citation(value)
            for span in trace.spans
            for value in (
                span.input,
                span.output,
                span.tool_result,
                span.metadata_json,
                span.content,
            )
        )
        if not has_citation:
            findings.append(
                Finding(
                    id=str(uuid4()),
                    trace_id=trace.id,
                    span_id=answer_span.id if answer_span is not None else None,
                    source="rule",
                    rule_code="missing_citation",
                    severity="medium",
                    category="证据完整性",
                    title="引用来源缺失",
                    explanation=(
                        "最终回答声称依据资料或研究，但全链路未找到 URL、source 或 citation 证据。"
                    ),
                    evidence={
                        "span_id": answer_span.id if answer_span is not None else None,
                        "answer": answer,
                    },
                )
            )
    tool_evidence = [
        value
        for span in trace.spans
        if span.kind in {"tool", "tool_result"}
        for result in (span.tool_result, span.output)
        if result is not None
        for value in evidence_values(result)
    ]
    supported_numbers = {
        normalized
        for value in tool_evidence
        for match in NUMBER_RE.findall(str(value))
        if (normalized := normalized_number(match)) is not None
    }
    unsupported = []
    if tool_evidence:
        for match in NUMBER_RE.findall(answer):
            normalized = normalized_number(match)
            if (
                normalized is not None
                and normalized not in supported_numbers
                and match not in unsupported
            ):
                unsupported.append(match)
    if unsupported:
        findings.append(
            Finding(
                id=str(uuid4()),
                trace_id=trace.id,
                span_id=answer_span.id if answer_span is not None else None,
                source="rule",
                rule_code="unsupported_number",
                severity="low",
                category="事实一致性",
                title="未支撑数字",
                explanation="最终回答包含工具证据中未出现的数字，需要人工复核；这不等同于判定为幻觉。",
                evidence={
                    "span_id": answer_span.id if answer_span is not None else None,
                    "numbers": unsupported,
                },
            )
        )
    negative_claim = re.search(r"不可用|不存在|未找到|失败|无效", answer)
    positive_claim = re.search(r"(?<!不)可用|(?<!不)存在|已找到|成功|有效", answer)
    conflict = None
    if answer and not re.search(r"不是不|并非不", answer):
        for tool_span in trace.spans:
            if tool_span.kind not in {"tool", "tool_result"}:
                continue
            for field, expected in explicit_fields(tool_span.tool_result):
                field_name = field.rsplit(".", 1)[-1].lower()
                if not isinstance(expected, bool) or field_name not in {
                    "available",
                    "success",
                    "found",
                    "exists",
                    "valid",
                    "enabled",
                }:
                    status_value = str(expected).lower()
                    status_conflict = field_name == "status" and (
                        (
                            status_value in {"cancelled", "canceled", "已取消"}
                            and re.search(r"已完成|成功", answer)
                        )
                        or (
                            status_value in {"completed", "success", "succeeded", "已完成"}
                            and re.search(r"已取消|取消成功", answer)
                        )
                    )
                    if not status_conflict:
                        continue
                if (
                    not isinstance(expected, bool)
                    or (expected and negative_claim)
                    or (not expected and positive_claim and not negative_claim)
                ):
                    conflict = (tool_span, field, expected)
                    break
            if conflict:
                break
    if conflict:
        tool_span, field, expected = conflict
        findings.append(
            Finding(
                id=str(uuid4()),
                trace_id=trace.id,
                span_id=answer_span.id if answer_span is not None else None,
                source="rule",
                rule_code="fact_conflict",
                severity="high",
                category="事实一致性",
                title="事实与工具结果冲突",
                explanation=f"最终回答与工具明确字段 {field}={expected} 的含义冲突。",
                evidence={
                    "span_id": answer_span.id if answer_span is not None else None,
                    "tool_span_id": tool_span.id,
                    "field": field,
                    "expected": expected,
                    "answer": answer,
                },
            )
        )
    repeated: dict[tuple[str, str], list] = {}
    for span in trace.spans:
        if span.kind != "tool" or not span.tool_name:
            continue
        arguments = span.tool_arguments
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                pass
        key = (
            span.tool_name,
            json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str),
        )
        repeated.setdefault(key, []).append(span)
    for (tool_name, arguments), spans in repeated.items():
        if len(spans) < 3:
            continue
        findings.append(
            Finding(
                id=str(uuid4()),
                trace_id=trace.id,
                span_id=spans[-1].id,
                source="rule",
                rule_code="repeated_tool_call",
                severity="medium",
                category="执行效率",
                title="工具调用重复",
                explanation=f"工具 {tool_name} 以相同参数连续或重复调用了 {len(spans)} 次。",
                evidence={
                    "span_id": spans[-1].id,
                    "span_ids": [span.id for span in spans],
                    "tool_name": tool_name,
                    "tool_arguments": json.loads(arguments),
                    "count": len(spans),
                },
            )
        )
    session.add_all(findings)
    session.flush()
    return len(findings)
