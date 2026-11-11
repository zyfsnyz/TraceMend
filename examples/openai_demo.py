"""把 OpenAI Python SDK 的 base_url 指向 AgentTrace。"""

import os

from openai import OpenAI


api_key = os.environ.get("OPENAI_API_KEY")
if not api_key:
    raise SystemExit("请先设置 OPENAI_API_KEY；密钥只发送给配置的真实上游。")

client = OpenAI(
    api_key=api_key,
    base_url=os.environ.get("AGENTTRACE_BASE", "http://127.0.0.1:8000/v1"),
    default_headers={
        "X-Upstream-Base": os.environ.get(
            "UPSTREAM_BASE", "https://api.openai.com/v1"
        )
    },
)

response = client.chat.completions.with_raw_response.create(
    model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
    messages=[{"role": "user", "content": "用一句话介绍杭州。"}],
)
print("X-AgentTrace-Id:", response.headers.get("X-AgentTrace-Id"))
print(response.parse().choices[0].message.content)
