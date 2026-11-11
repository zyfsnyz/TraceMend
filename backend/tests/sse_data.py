import json


def sse_payloads():
    chunks = [
        {"choices": [{"delta": {"content": "明天"}, "index": 0}]},
        {"choices": [{"delta": {"reasoning_content": "需要"}, "index": 0}]},
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-weather",
                                "type": "function",
                                "function": {"name": "weather", "arguments": '{"ci'},
                            }
                        ]
                    },
                    "index": 0,
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "content": " 25°C",
                        "tool_calls": [{"index": 0, "function": {"arguments": 'ty":"北京"}'}}],
                    },
                    "index": 0,
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 1,
                                "id": "call-calendar",
                                "type": "function",
                                "function": {"name": "calendar", "arguments": "{}"},
                            }
                        ]
                    },
                    "index": 0,
                }
            ]
        },
        {
            "choices": [{"delta": {}, "finish_reason": "tool_calls", "index": 0}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 9, "total_tokens": 29},
        },
    ]
    events = [f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode() for chunk in chunks]
    events.append(b"data: [DONE]\n\n")
    raw = b"".join(events)
    return [raw[index : index + 37] for index in range(0, len(raw), 37)]
