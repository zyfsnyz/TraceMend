import socket
import threading
import time
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi import FastAPI, Request


@pytest.fixture
def mcp_upstream():
    application = FastAPI()
    requests = []

    @application.post("/mcp")
    async def mcp(request: Request):
        payload = await request.json()
        requests.append(
            {"payload": payload, "headers": dict(request.headers), "path": request.url.path}
        )
        method = payload.get("method")
        base = {"jsonrpc": "2.0", "id": payload.get("id")}
        if method == "initialize":
            return {
                **base,
                "result": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "local"}},
            }
        if method == "tools/list":
            return {
                **base,
                "result": {"tools": [{"name": "weather", "inputSchema": {"type": "object"}}]},
            }
        if method == "tools/call" and payload.get("params", {}).get("name") == "explode":
            return {**base, "error": {"code": -32000, "message": "工具爆炸"}}
        if method == "tools/call":
            return {
                **base,
                "result": {"content": [{"type": "text", "text": "天气晴"}], "source": "local"},
            }
        return {**base, "error": {"code": -32601, "message": "方法不存在"}}

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(application, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.01)
    if not server.started:
        raise RuntimeError("MCP 本地上游启动失败")
    yield SimpleNamespace(url=f"http://127.0.0.1:{port}/mcp", requests=requests)
    server.should_exit = True
    thread.join(timeout=10)
