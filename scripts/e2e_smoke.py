#!/usr/bin/env python3
"""真实代理→本地上游→落库→报告的可重复验收；仅使用 Python 标准库。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from contextlib import ExitStack
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


NONSTREAM_MARKER = "SMOKE_NONSTREAM_杭州_7f3a"
STREAM_MARKER = "SMOKE_STREAM_苏州_91bc"
MCP_MARKER = "SMOKE_MCP_天气_2d8e"


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


class MockOpenAIHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802 - http.server 接口
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length) or b"{}")
        if self.path != "/v1/chat/completions":
            self.send_error(404, "expected /v1/chat/completions")
            return
        if request.get("stream"):
            self._stream(request)
        else:
            self._complete(request)

    def _complete(self, request: dict[str, Any]) -> None:
        body = json_bytes({
            "id": "chatcmpl-smoke-nonstream",
            "object": "chat.completion",
            "created": 1762862400,
            "model": request.get("model", "smoke-model"),
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": f"非流式验收成功：{NONSTREAM_MARKER}",
                },
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        })
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _stream(self, request: dict[str, Any]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        chunks = [
            {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "流式验收："}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {"content": STREAM_MARKER[:18]}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {"content": STREAM_MARKER[18:]}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 13, "completion_tokens": 9, "total_tokens": 22}},
        ]
        for item in chunks:
            item.setdefault("id", "chatcmpl-smoke-stream")
            item.setdefault("object", "chat.completion.chunk")
            item.setdefault("created", 1762862400)
            item.setdefault("model", request.get("model", "smoke-model"))
            self.wfile.write(b"data: " + json_bytes(item) + b"\n\n")
            self.wfile.flush()
            time.sleep(0.02)
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, format: str, *args: Any) -> None:
        pass


class MockMCPHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802 - http.server 接口
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length) or b"{}")
        if self.path != "/mcp" or request.get("method") != "tools/call":
            self.send_error(404, "expected MCP tools/call at /mcp")
            return
        body = json_bytes({
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "content": [{"type": "text", "text": f"杭州晴，26°C；{MCP_MARKER}"}],
                "structuredContent": {"city": "杭州", "condition": "晴", "temperature_c": 26},
                "isError": False,
            },
        })
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        pass


@dataclass
class Response:
    status: int
    headers: Any
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


def request(
    method: str,
    url: str,
    payload: Any = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> Response:
    all_headers = dict(headers or {})
    data = None
    if payload is not None:
        data = json_bytes(payload)
        all_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=all_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return Response(response.status, response.headers, response.read())
    except urllib.error.HTTPError as exc:
        return Response(exc.code, exc.headers, exc.read())


def expect_status(response: Response, expected: int = 200) -> None:
    if response.status != expected:
        body = response.body.decode("utf-8", errors="replace")
        raise AssertionError(f"期望 HTTP {expected}，实际 {response.status}: {body}")


def trace_header(response: Response, expected_id: str) -> None:
    actual = response.headers.get("X-AgentTrace-Id")
    if actual != expected_id:
        raise AssertionError(f"X-AgentTrace-Id 期望 {expected_id}，实际 {actual!r}")


def sse_content(body: bytes) -> str:
    """拼接 SSE data 中的 assistant delta，避免把 JSON 分隔符当成内容。"""
    parts: list[str] = []
    for line in body.decode("utf-8").splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        event = json.loads(line[6:])
        for choice in event.get("choices", []):
            content = (choice.get("delta") or {}).get("content")
            if content:
                parts.append(content)
    return "".join(parts)


def start_server(stack: ExitStack, handler: type[BaseHTTPRequestHandler]) -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    stack.callback(server.server_close)
    stack.callback(server.shutdown)
    return f"http://127.0.0.1:{server.server_port}"


class SmokeTest:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.admin_headers = {}
        if os.environ.get("ADMIN_KEY"):
            self.admin_headers["X-Admin-Key"] = os.environ["ADMIN_KEY"]
        self.trace_ids: list[str] = []

    def api(self, method: str, path: str) -> Response:
        return request(method, self.base_url + path, headers=self.admin_headers, timeout=self.timeout)

    def wait_detail(self, trace_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        latest: dict[str, Any] = {}
        while time.monotonic() < deadline:
            response = self.api("GET", f"/api/traces/{trace_id}")
            if response.status == 200:
                latest = response.json()
                if latest.get("spans"):
                    return latest
            elif response.status != 404:
                expect_status(response)
            time.sleep(0.2)
        raise AssertionError(f"Trace {trace_id} 在 {self.timeout}s 内未落库或没有 spans：{latest}")

    def new_id(self) -> str:
        trace_id = str(uuid.uuid4())
        self.trace_ids.append(trace_id)
        return trace_id

    def assert_usage(self, detail: dict[str, Any]) -> None:
        trace = detail.get("trace") or {}
        candidates = [trace.get("total_tokens")]
        candidates.extend((item.get("total_tokens") for item in detail.get("spans", [])))
        if not any(isinstance(value, (int, float)) and value > 0 for value in candidates):
            raise AssertionError(f"未找到正数 total_tokens：{candidates}")

    def assert_marker(self, detail: dict[str, Any], marker: str) -> None:
        serialized = json.dumps(detail, ensure_ascii=False)
        if marker not in serialized:
            raise AssertionError(f"Trace 详情未保存标记 {marker}")

    def cleanup(self) -> None:
        for trace_id in reversed(self.trace_ids):
            response = self.api("DELETE", f"/api/traces/{trace_id}")
            if response.status not in (200, 204, 404):
                expect_status(response)
            print(f"[清理] 已删除 {trace_id}")


def run(base_url: str, timeout: float, cleanup: bool) -> None:
    smoke = SmokeTest(base_url, timeout)
    artifact_dir = Path(__file__).resolve().parent.parent / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    with ExitStack() as stack:
        openai_base = start_server(stack, MockOpenAIHandler)
        mcp_url = start_server(stack, MockMCPHandler) + "/mcp"
        print(f"[1/7] 本地模拟上游已启动（仅 127.0.0.1，随机端口）")

        health = smoke.api("GET", "/api/health")
        expect_status(health)
        print(f"[2/7] AgentTrace 健康检查通过：{health.body.decode('utf-8', errors='replace')}")

        nonstream_id = smoke.new_id()
        nonstream = request(
            "POST", smoke.base_url + "/v1/chat/completions",
            {
                "model": "smoke-openai-v1",
                "messages": [{"role": "user", "content": f"请原样返回 {NONSTREAM_MARKER}"}],
            },
            {"X-Upstream-Base": openai_base + "/v1", "X-AgentTrace-Id": nonstream_id},
            timeout,
        )
        expect_status(nonstream)
        trace_header(nonstream, nonstream_id)
        if NONSTREAM_MARKER not in nonstream.body.decode("utf-8"):
            raise AssertionError("非流式响应缺少上游标记")
        detail = smoke.wait_detail(nonstream_id)
        if not detail.get("spans"):
            raise AssertionError("非流式 Trace 没有 spans")
        smoke.assert_marker(detail, NONSTREAM_MARKER)
        smoke.assert_usage(detail)
        if (detail.get("trace") or {}).get("model") != "smoke-openai-v1":
            raise AssertionError("非流式 Trace 未保存请求模型")
        print(f"[3/7] 非流式代理及落库通过：trace_id={nonstream_id}")

        stream_id = smoke.new_id()
        stream = request(
            "POST", smoke.base_url + "/v1/chat/completions",
            {
                "model": "smoke-openai-stream-v1",
                "stream": True,
                "stream_options": {"include_usage": True},
                "messages": [{"role": "user", "content": f"请原样返回 {STREAM_MARKER}"}],
            },
            {"X-Upstream-Base": openai_base + "/v1", "X-AgentTrace-Id": stream_id},
            timeout,
        )
        expect_status(stream)
        trace_header(stream, stream_id)
        stream_text = stream.body.decode("utf-8")
        if STREAM_MARKER not in sse_content(stream.body) or "data: [DONE]" not in stream_text:
            raise AssertionError("SSE 响应缺少完整内容或 [DONE]")
        detail = smoke.wait_detail(stream_id)
        if not detail.get("spans"):
            raise AssertionError("流式 Trace 没有 spans")
        smoke.assert_marker(detail, STREAM_MARKER)
        smoke.assert_usage(detail)
        print(f"[4/7] SSE 流式代理、内容及 Token 落库通过：trace_id={stream_id}")

        mcp_id = smoke.new_id()
        mcp = request(
            "POST", smoke.base_url + "/mcp",
            {
                "jsonrpc": "2.0", "id": 7, "method": "tools/call",
                "params": {"name": "weather.lookup", "arguments": {"city": "杭州"}},
            },
            {
                "Accept": "application/json, text/event-stream",
                "X-MCP-Upstream": mcp_url,
                "X-AgentTrace-Id": mcp_id,
            },
            timeout,
        )
        expect_status(mcp)
        trace_header(mcp, mcp_id)
        if MCP_MARKER not in mcp.body.decode("utf-8"):
            raise AssertionError("MCP 响应缺少工具返回标记")
        detail = smoke.wait_detail(mcp_id)
        smoke.assert_marker(detail, MCP_MARKER)
        serialized = json.dumps(detail, ensure_ascii=False)
        if "weather.lookup" not in serialized or "杭州" not in serialized:
            raise AssertionError("MCP Trace 未保存工具名或调用参数")
        tool_kinds = {"tool", "tool_result", "mcp"}
        if not any(item.get("kind") in tool_kinds for item in detail.get("spans", [])):
            raise AssertionError("MCP Trace 没有 tool/tool_result/mcp span")
        print(f"[5/7] MCP tools/call、参数及工具返回落库通过：trace_id={mcp_id}")

        markdown = smoke.api("GET", f"/api/traces/{nonstream_id}/report.md")
        expect_status(markdown)
        markdown_text = markdown.body.decode("utf-8")
        if not re.search(r"(?m)^#\s+\S", markdown_text):
            raise AssertionError("Markdown 报告没有一级标题")
        md_path = artifact_dir / f"smoke-{nonstream_id}-report.md"
        md_path.write_bytes(markdown.body)

        pdf = smoke.api("GET", f"/api/traces/{nonstream_id}/report.pdf")
        expect_status(pdf)
        if not pdf.body.startswith(b"%PDF"):
            raise AssertionError("PDF 报告缺少 %PDF 魔数")
        pdf_path = artifact_dir / f"smoke-{nonstream_id}-report.pdf"
        pdf_path.write_bytes(pdf.body)
        print(f"[6/7] Markdown/PDF 报告通过：{md_path}；{pdf_path}")

        if cleanup:
            smoke.cleanup()
        else:
            print("[7/7] 验收 Trace 已保留供界面查看（使用 --cleanup 可删除）")
        print("PASS：OpenAI 非流式、SSE、MCP、持久化与报告全流程验收通过。")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=30.0, help="单次请求及落库轮询超时秒数")
    parser.add_argument("--cleanup", action="store_true", help="验收结束后删除本次创建的 Trace")
    args = parser.parse_args()
    try:
        run(args.base_url, args.timeout, args.cleanup)
        return 0
    except (AssertionError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
