"""把 OpenAI Python SDK 的 base_url 指向 TraceMend。

密钥会先发送给所配置的 TraceMend 实例，再由其转发给上游；只使用可信实例。
远程实例必须使用 TLS（https），不要在明文远程连接中提供真实密钥。
"""

import os

from openai import OpenAI

api_key = os.environ.get("OPENAI_API_KEY")
if not api_key:
    raise SystemExit("请先设置 OPENAI_API_KEY；密钥会先发送给所配置的 TraceMend，再转发上游。")

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
