import asyncio
import socket
import threading
import time
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sse_data import sse_payloads


@pytest.fixture
def openai_upstream():
    application = FastAPI()
    requests = []

    @application.post("/v1/chat/completions")
    async def completions(request: Request):
        payload = await request.json()
        requests.append({"payload": payload, "headers": dict(request.headers)})
        if payload.get("model") == "denied-model":
            return JSONResponse(status_code=429, content={"error": {"message": "上游限流"}})
        if payload.get("model") == "html-model":
            return Response(
                content="<html><body>上游维护中</body></html>", media_type="text/html"
            )
        if payload.get("model") == "plain-model":
            return Response(content="纯文本结果", media_type="text/plain")
        if payload.get("model") == "empty-model":
            return Response(content=b"", media_type="application/octet-stream")
        if payload.get("model") == "denied-html-model":
            return Response(
                content="<html>Bad Gateway</html>", status_code=502, media_type="text/html"
            )
        if payload.get("stream"):

            async def generate():
                for event in sse_payloads():
                    yield event
                    await asyncio.sleep(0)

            return StreamingResponse(generate(), media_type="text/event-stream")
        return {
            "id": "chatcmpl-local",
            "object": "chat.completion",
            "model": payload.get("model"),
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "明天 25°C",
                        "reasoning_content": "查过天气",
                        "tool_calls": [
                            {
                                "id": "call-weather",
                                "type": "function",
                                "function": {"name": "weather", "arguments": '{"city":"北京"}'},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        }

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(application, log_level="error", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.01)
    if not server.started:
        raise RuntimeError("本地上游启动失败")
    yield SimpleNamespace(base_url=f"http://127.0.0.1:{port}/v1", requests=requests)
    server.should_exit = True
    thread.join(timeout=10)
