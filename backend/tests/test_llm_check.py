import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


class ModelHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - http.server 协议方法
        length = int(self.headers.get("content-length", "0"))
        raw = self.rfile.read(length)
        self.server.requests.append(
            {"path": self.path, "headers": dict(self.headers), "body": raw.decode()}
        )
        if self.server.status >= 400:
            payload = {"error": "mock failure"}
        else:
            payload = {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": self.server.reply,
                        }
                    }
                ]
            }
        body = json.dumps(payload).encode()
        self.send_response(self.server.status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


@contextmanager
def model_server(reply: str = '{"findings": []}', status: int = 200):
    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
    server.reply = reply
    server.status = status
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest_asyncio.fixture
async def llm_client(tmp_path, monkeypatch):
    with model_server() as server:
        monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'llm.db'}")
        monkeypatch.setenv("ENABLE_LLM_CHECK", "true")
        monkeypatch.setenv("CHECK_MODEL_BASE", f"http://127.0.0.1:{server.server_port}/v1")
        monkeypatch.setenv("CHECK_MODEL_KEY", "test-check-key")
        monkeypatch.setenv("CHECK_MODEL_NAME", "audit-mini")
        from app.main import create_app

        application = create_app()
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            yield client, server


async def seed(client):
    payload = {
        "trace": {
            "id": "trace-llm",
            "name": "订单查询",
            "source": "pytest",
            "output": {"content": "订单已成功发货"},
            "total_tokens": 30,
        },
        "spans": [
            {
                "id": "span-tool",
                "trace_id": "trace-llm",
                "sequence": 0,
                "kind": "tool",
                "name": "查询订单",
                "status": "error",
                "tool_name": "query_order",
                "tool_arguments": {"order_id": "A-1"},
                "tool_result": {"status": "failed", "message": "仓库缺货"},
                "error": "仓库系统返回失败",
            },
            {
                "id": "span-llm",
                "trace_id": "trace-llm",
                "parent_span_id": "span-tool",
                "sequence": 1,
                "kind": "llm",
                "name": "最终回答",
                "content": "订单已成功发货",
                "total_tokens": 30,
            },
        ],
    }
    response = await client.post("/api/traces/ingest", json=payload)
    assert response.status_code == 200
    assert response.json()["finding_count"] >= 1


@pytest.mark.asyncio
async def test_llm_check_persists_validated_findings_and_replaces_previous(llm_client):
    client, server = llm_client
    await seed(client)
    server.reply = """```json
{"findings": [
  {"span_id": "span-llm", "severity": "high", "category": "事实冲突",
   "title": "回答与工具返回矛盾", "explanation": "工具返回失败，回答却说已成功发货。",
   "evidence": "tool_result.status=failed; final=已成功发货"},
  {"span_id": "not-a-real-span", "severity": "medium", "category": "引用缺失",
   "title": "无来源", "explanation": "回答缺来源。", "evidence": ""}
]}
```"""

    response = await client.post("/api/traces/trace-llm/llm-check")
    assert response.status_code == 200
    result = response.json()
    assert result["trace_id"] == "trace-llm"
    assert result["finding_count"] >= 2

    detail = (await client.get("/api/traces/trace-llm")).json()
    llm_findings = [item for item in detail["findings"] if item["source"] == "llm"]
    assert len(llm_findings) == 2
    assert {item["severity"] for item in llm_findings} == {"high", "medium"}
    assert {item["span_id"] for item in llm_findings} == {"span-llm", None}
    assert any(item["source"] == "rule" for item in detail["findings"])
    assert detail["trace"]["metadata"]["llm_check"]["status"] == "completed"
    assert "test-check-key" not in json.dumps(detail, ensure_ascii=False)

    assert len(server.requests) == 1
    sent = server.requests[0]
    assert sent["path"] == "/v1/chat/completions"
    assert sent["headers"]["Authorization"] == "Bearer test-check-key"
    request_body = json.loads(sent["body"])
    assert request_body["model"] == "audit-mini"
    prompt = json.dumps(request_body, ensure_ascii=False)
    assert "query_order" in prompt
    assert "仓库缺货" in prompt
    assert "test-check-key" not in prompt

    server.reply = '{"findings": []}'
    second = await client.post("/api/traces/trace-llm/llm-check")
    assert second.status_code == 200
    detail = (await client.get("/api/traces/trace-llm")).json()
    assert not [item for item in detail["findings"] if item["source"] == "llm"]
    assert any(item["source"] == "rule" for item in detail["findings"])


@pytest.mark.asyncio
async def test_llm_check_disabled_and_missing_key_are_409(client):
    disabled = await client.post("/api/traces/any/llm-check")
    assert disabled.status_code == 409
    assert "未启用" in disabled.json()["detail"]

    settings = client._transport.app.state.settings
    settings.enable_llm_check = True
    settings.check_model_key = None
    missing = await client.post("/api/traces/any/llm-check")
    assert missing.status_code == 409
    assert "未配置" in missing.json()["detail"]


@pytest.mark.asyncio
async def test_llm_check_upstream_failure_preserves_rules_and_hides_key(llm_client):
    client, server = llm_client
    await seed(client)
    server.status = 500
    response = await client.post("/api/traces/trace-llm/llm-check")
    assert response.status_code == 502
    assert "检测模型调用失败" in response.json()["detail"]
    assert "test-check-key" not in response.text

    detail = (await client.get("/api/traces/trace-llm")).json()
    assert any(item["source"] == "rule" for item in detail["findings"])
    assert not [item for item in detail["findings"] if item["source"] == "llm"]
    assert detail["trace"]["metadata"]["llm_check"]["status"] == "failed"
    assert "test-check-key" not in json.dumps(detail, ensure_ascii=False)
