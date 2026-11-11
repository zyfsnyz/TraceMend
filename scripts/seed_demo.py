#!/usr/bin/env python3
"""通过真实 ingest API 写入两条中文演示 Trace；仅使用 Python 标准库。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


NAMESPACE = uuid.UUID("72f68136-1401-4b29-a1fc-e970996a594b")


def stable_id(label: str) -> str:
    return str(uuid.uuid5(NAMESPACE, label))


def moment(offset_ms: int) -> str:
    base = datetime(2026, 11, 11, 2, 0, tzinfo=timezone.utc)
    return (base + timedelta(milliseconds=offset_ms)).isoformat().replace("+00:00", "Z")


def span(
    label: str,
    sequence: int,
    kind: str,
    name: str,
    start: int,
    end: int,
    parent: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": stable_id(label),
        "parent_span_id": stable_id(parent) if parent else None,
        "sequence": sequence,
        "kind": kind,
        "name": name,
        "status": "completed",
        "started_at": moment(start),
        "ended_at": moment(end),
        "duration_ms": end - start,
    }
    item.update(fields)
    return item


def travel_trace() -> dict[str, Any]:
    question = "我明天从杭州去上海，天气适合出行吗？帮我安排一条简洁路线。"
    answer = "明天杭州多云、气温 18–25°C，上海阵雨、气温 19–23°C。建议携带雨具，乘 G7502 次列车到上海虹桥，再换乘地铁 2 号线前往人民广场。"
    return {
        "trace": {
            "id": stable_id("demo-travel-trace"),
            "name": "【演示】天气与旅行工具链",
            "source": "custom-agent",
            "status": "completed",
            "model": "demo-travel-planner-v1",
            "input": {"messages": [{"role": "user", "content": question}]},
            "output": {"content": answer},
            "started_at": moment(0),
            "ended_at": moment(4200),
            "duration_ms": 4200,
            "prompt_tokens": 176,
            "completion_tokens": 129,
            "total_tokens": 305,
            "tags": ["demo", "中文", "工具链", "正常链路"],
            "metadata": {
                "demo": True,
                "description": "虚构但结构完整的演示数据，不来自真实用户或实时天气服务。",
                "environment": "seed_demo",
            },
        },
        "spans": [
            span(
                "demo-travel-user", 0, "user", "用户旅行咨询", 0, 20,
                input={"content": question}, content=question,
            ),
            span(
                "demo-travel-llm-weather", 1, "llm", "规划天气查询", 20, 320,
                parent="demo-travel-user",
                input={"messages": [{"role": "user", "content": question}]},
                output={"tool_calls": [{"name": "weather.lookup", "arguments": {"cities": ["杭州", "上海"], "date": "2026-11-12"}}]},
                content="需要先查询两地天气。",
                model="demo-travel-planner-v1",
                prompt_tokens=96, completion_tokens=28, total_tokens=124,
            ),
            span(
                "demo-travel-tool-weather", 2, "tool", "查询杭州与上海天气", 320, 350,
                parent="demo-travel-llm-weather",
                tool_name="weather.lookup",
                tool_arguments={"cities": ["杭州", "上海"], "date": "2026-11-12"},
                input={"cities": ["杭州", "上海"], "date": "2026-11-12"},
                metadata={"tool_schema": {"type": "object", "required": ["cities", "date"]}},
            ),
            span(
                "demo-travel-result-weather", 3, "tool_result", "返回结构化天气", 350, 980,
                parent="demo-travel-tool-weather",
                tool_name="weather.lookup",
                tool_result={
                    "source_url": "https://weather.example/demo/2026-11-12",
                    "observations": [
                        {"city": "杭州", "condition": "多云", "temperature_c": {"low": 18, "high": 25}},
                        {"city": "上海", "condition": "阵雨", "temperature_c": {"low": 19, "high": 23}},
                    ],
                },
                output={"status": "ok"},
            ),
            span(
                "demo-travel-llm-route", 4, "llm", "规划铁路与市内路线", 980, 1220,
                parent="demo-travel-result-weather",
                input={"weather_summary": "杭州多云，上海阵雨"},
                output={"tool_calls": [{"name": "travel.route", "arguments": {"from": "杭州", "to": "上海人民广场", "date": "2026-11-12"}}]},
                content="结合天气规划铁路与地铁路线。",
                model="demo-travel-planner-v1",
                prompt_tokens=48, completion_tokens=31, total_tokens=79,
            ),
            span(
                "demo-travel-tool-route", 5, "tool", "查询旅行路线", 1220, 1250,
                parent="demo-travel-llm-route",
                tool_name="travel.route",
                tool_arguments={"from": "杭州", "to": "上海人民广场", "date": "2026-11-12"},
                metadata={"tool_schema": {"type": "object", "required": ["from", "to", "date"]}},
            ),
            span(
                "demo-travel-result-route", 6, "tool_result", "返回车次与换乘建议", 1250, 2050,
                parent="demo-travel-tool-route",
                tool_name="travel.route",
                tool_result={
                    "source_url": "https://rail.example/demo/G7502",
                    "segments": [
                        {"mode": "rail", "train": "G7502", "from": "杭州东", "to": "上海虹桥", "departure": "08:15", "arrival": "09:08"},
                        {"mode": "metro", "line": "2号线", "from": "上海虹桥", "to": "人民广场"},
                    ],
                },
            ),
            span(
                "demo-travel-llm-final", 7, "llm", "综合天气与路线作答", 2050, 4200,
                parent="demo-travel-result-route",
                input={"weather": "已获取", "route": "已获取"},
                output={"content": answer}, content=answer,
                model="demo-travel-planner-v1",
                prompt_tokens=32, completion_tokens=70, total_tokens=102,
                metadata={"citations": ["https://weather.example/demo/2026-11-12", "https://rail.example/demo/G7502"]},
            ),
        ],
    }


def history_trace() -> dict[str, Any]:
    question = "普津汉大学哪一年建校？现在有多少个学院？请给出带可靠来源的校史介绍。"
    answer = "普津汉大学创立于 1897 年，现有 36 个学院和 42817 名学生，是西北地区历史最悠久的大学。学校图书馆藏书 650 万册。[来源：普津汉大学官网]"
    repeated_args = {"query": "普津汉大学 校史", "top_k": 5}
    spans = [
        span(
            "demo-history-user", 0, "user", "用户要求有来源的校史回答", 0, 20,
            input={"content": question}, content=question,
        ),
        span(
            "demo-history-llm-plan", 1, "llm", "规划校史检索", 20, 260,
            parent="demo-history-user",
            input={"messages": [{"role": "user", "content": question}]},
            output={"tool_calls": [{"name": "campus.knowledge_search", "arguments": repeated_args}]},
            content="先检索学校知识库。",
            model="demo-campus-qa-v1", prompt_tokens=84, completion_tokens=24, total_tokens=108,
        ),
    ]
    for index, (start, end) in enumerate(((260, 790), (790, 1320), (1320, 1850))):
        call_label = f"demo-history-tool-{index + 1}"
        result_label = f"demo-history-result-{index + 1}"
        spans.append(
            span(
                call_label, 2 + index * 2, "tool", f"重复检索校史知识库（第 {index + 1} 次）",
                start, start + 20, parent="demo-history-llm-plan",
                tool_name="campus.knowledge_search", tool_arguments=repeated_args,
                input=repeated_args,
                metadata={"tool_schema": {"type": "object", "required": ["query", "top_k"]}},
            )
        )
        spans.append(
            span(
                result_label, 3 + index * 2, "tool_result", f"知识库返回空结果（第 {index + 1} 次）",
                start + 20, end, parent=call_label,
                tool_name="campus.knowledge_search", tool_result={"items": [], "total": 0},
                output={"items": []},
            )
        )
    spans.append(
        span(
            "demo-history-llm-final", 8, "llm", "在无证据情况下生成校史答案", 1850, 3600,
            parent="demo-history-result-3",
            input={"retrieval": "三次检索均无结果"},
            output={"content": answer}, content=answer,
            model="demo-campus-qa-v1", prompt_tokens=142, completion_tokens=116, total_tokens=258,
        )
    )
    return {
        "trace": {
            "id": stable_id("demo-history-trace"),
            "name": "【演示】普津汉大学校史问答风险",
            "source": "custom-agent",
            "status": "completed",
            "model": "demo-campus-qa-v1",
            "input": {"messages": [{"role": "user", "content": question}]},
            "output": {"content": answer},
            "started_at": moment(0),
            "ended_at": moment(3600),
            "duration_ms": 3600,
            "prompt_tokens": 226,
            "completion_tokens": 140,
            "total_tokens": 366,
            "tags": ["demo", "中文", "重复调用", "空返回", "引用缺失", "未支撑数字"],
            "metadata": {
                "demo": True,
                "description": "普津汉大学及所有工具数据均为演示虚构，用于展示风险证据，不是真实校史。",
                "expected_risks": ["重复调用", "空工具结果", "无来源引用", "未支撑数字"],
            },
        },
        "spans": spans,
    }


def ingest(base_url: str, payload: dict[str, Any], admin_key: str, timeout: float) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if admin_key:
        headers["X-Admin-Key"] = admin_key
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/traces/ingest",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ingest 返回 HTTP {exc.code}: {body}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    admin_key = os.environ.get("ADMIN_KEY", "")
    for payload in (travel_trace(), history_trace()):
        result = ingest(args.base_url, payload, admin_key, args.timeout)
        print(
            f"{payload['trace']['name']}\n"
            f"  trace_id={result['trace_id']}\n"
            f"  finding_count={result['finding_count']}"
        )
    print("演示数据已写入，可在 AgentTrace 界面按 demo 标签查看。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, KeyError, ValueError) as exc:
        print(f"写入演示数据失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
