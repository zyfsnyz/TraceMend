import json
import re
from typing import Any

import httpx

from .config import Settings
from .models import Trace
from .sensitive import redact_sensitive

SYSTEM_PROMPT = """你是严谨的 AI Agent 审计助手。
请只依据给定 Trace 中的最终回答、工具参数和工具原始返回，
保守地标记以下问题：事实冲突、编造工具未返回的信息、逻辑矛盾、引用来源缺失。
规则：
1. 没有明确证据时不要报告；不要因为信息缺失就断言幻觉。
2. 工具返回失败但回答声称成功，是典型事实冲突，应报告。
3. 只输出严格 JSON，不要 Markdown 或额外解释。
4. 输出格式：{"findings":[{"span_id":"现有 Span ID 或 null","severity":"high|medium|low",
   "category":"简短中文类别","title":"简短中文标题","explanation":"基于证据的中文解释",
   "evidence":"引用相关工具返回或回答片段"}]}
5. 最多 20 条，按严重度排序；证据不足时返回 {"findings":[]}。"""


class LLMCheckError(Exception):
    """Safe, credential-free error surfaced as HTTP 502."""


def clip(value: Any, limit: int = 4000) -> Any:
    value = redact_sensitive(value)
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…[截断]"
    if isinstance(value, (dict, list)):
        serialized = json.dumps(value, ensure_ascii=False, default=str)
        if len(serialized) > limit:
            return serialized[:limit] + "…[截断]"
    return value


def trace_context(trace: Trace) -> dict[str, Any]:
    return {
        "trace": {
            "id": trace.id,
            "name": trace.name,
            "model": trace.model,
            "status": trace.status,
            "input": clip(trace.input),
            "output": clip(trace.output),
            "error": clip(trace.error),
        },
        "spans": [
            {
                "span_id": span.id,
                "sequence": span.sequence,
                "kind": span.kind,
                "name": span.name,
                "status": span.status,
                "content": clip(span.content),
                "reasoning": clip(span.reasoning),
                "tool_name": span.tool_name,
                "tool_arguments": clip(span.tool_arguments),
                "tool_result": clip(span.tool_result),
                "error": clip(span.error),
            }
            for span in trace.spans
        ],
    }


def extract_json(content: str) -> dict[str, Any]:
    content = content.strip()
    content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE)
    content = re.sub(r"\s*```$", "", content)
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型未返回 JSON 对象")
    payload = json.loads(content[start : end + 1])
    if not isinstance(payload, dict) or not isinstance(payload.get("findings"), list):
        raise ValueError("模型 JSON 缺少 findings 数组")
    return payload


def normalize_findings(payload: dict[str, Any], valid_span_ids: set[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for raw in payload["findings"][:20]:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()[:160]
        explanation = str(raw.get("explanation") or "").strip()[:2000]
        if not title or not explanation:
            continue
        severity = str(raw.get("severity") or "medium").lower()
        if severity not in {"high", "medium", "low"}:
            severity = "medium"
        span_id = raw.get("span_id")
        if not isinstance(span_id, str) or span_id not in valid_span_ids:
            span_id = None
        evidence = raw.get("evidence")
        if not isinstance(evidence, dict):
            evidence = {"quote": str(evidence or "")[:1200]}
        findings.append(
            {
                "span_id": span_id,
                "rule_code": "llm_check",
                "severity": severity,
                "category": str(raw.get("category") or "事实一致性").strip()[:80],
                "title": title,
                "explanation": explanation,
                "evidence": redact_sensitive(evidence),
            }
        )
    return findings


async def run_llm_check(trace: Trace, settings: Settings) -> list[dict[str, Any]]:
    if not settings.check_model_key:
        raise LLMCheckError("检测模型未配置密钥")
    base = settings.check_model_base.rstrip("/")
    url = f"{base}/chat/completions"
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "请审计以下 Trace：\n"
            + json.dumps(trace_context(trace), ensure_ascii=False, default=str),
        },
    ]
    body = {
        "model": settings.check_model_name,
        "messages": messages,
        "temperature": 0,
    }
    try:
        async with httpx.AsyncClient(timeout=settings.check_model_timeout) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {settings.check_model_key}"},
                json=body,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        payload = extract_json(str(content))
        return normalize_findings(payload, {span.id for span in trace.spans})
    except Exception as exc:  # noqa: BLE001 - 统一包装为不含凭据的审计错误
        raise LLMCheckError(f"检测模型调用或解析失败（{type(exc).__name__}）") from exc
