"""通过 AgentTrace 转发一次 MCP tools/call；仅需 Python 标准库。"""

import json
import os
import urllib.request


upstream = os.environ.get("MCP_UPSTREAM_URL")
if not upstream:
    raise SystemExit("请先设置 MCP_UPSTREAM_URL，例如 http://127.0.0.1:9000/mcp")

payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
        "name": os.environ.get("MCP_TOOL_NAME", "get_weather"),
        "arguments": {"city": "杭州"},
    },
}
request = urllib.request.Request(
    os.environ.get("AGENTTRACE_MCP_URL", "http://127.0.0.1:8000/mcp"),
    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "X-MCP-Upstream": upstream,
    },
    method="POST",
)
with urllib.request.urlopen(request, timeout=60) as response:
    print("X-AgentTrace-Id:", response.headers.get("X-AgentTrace-Id"))
    print(response.read().decode("utf-8"))
