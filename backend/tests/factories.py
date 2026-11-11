from copy import deepcopy


def trace_payload(trace_id="trace-1"):
    return {
        "trace": {
            "id": trace_id,
            "name": "差旅助手",
            "source": "custom-agent",
            "status": "completed",
            "model": "demo-model",
            "input": {"messages": [{"role": "user", "content": "查询天气"}]},
            "output": {"content": "明天 25°C"},
            "started_at": "2026-11-11T10:00:00Z",
            "ended_at": "2026-11-11T10:00:02Z",
            "tags": ["demo", "travel"],
            "metadata": {"tenant": "local"},
        },
        "spans": [
            {
                "id": "span-user",
                "sequence": 0,
                "kind": "user",
                "name": "用户提问",
                "status": "completed",
                "input": {"content": "查询天气"},
                "content": "查询天气",
            },
            {
                "id": "span-llm",
                "parent_span_id": "span-user",
                "sequence": 1,
                "kind": "llm",
                "name": "模型回答",
                "status": "completed",
                "content": "明天 25°C",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "metadata": {"upstream_status": 200},
            },
        ],
    }


def updated_payload():
    payload = deepcopy(trace_payload())
    payload["trace"]["name"] = "更新后的助手"
    payload["trace"]["tags"] = ["updated"]
    payload["spans"] = [payload["spans"][0]]
    return payload
