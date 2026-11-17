import json

import httpx
from fastapi import APIRouter, HTTPException, Request

from .schemas import SettingsPatch, SettingsTestIn
from .settings_store import public_settings, save_settings

router = APIRouter(prefix="/api/settings")


@router.get("")
def get_settings(request: Request):
    return public_settings(request.app.state.settings)


@router.put("")
def put_settings(payload: SettingsPatch, request: Request):
    values = payload.model_dump(exclude_unset=True)
    settings = save_settings(request.app, values)
    return public_settings(settings)


def auth_headers(key: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"} if key else {}


async def test_openai_connection(
    base: str, model: str, key: str | None, timeout_seconds: float
):
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(
                f"{base.rstrip('/')}/chat/completions",
                headers=auth_headers(key),
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Reply only with OK."}],
                    "max_tokens": 8,
                    "temperature": 0,
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="无法连接模型服务，请检查地址和网络") from exc
    if not response.is_success:
        raise HTTPException(
            status_code=502, detail=f"模型服务返回 HTTP {response.status_code}，请检查模型和密钥"
        )
    return response


async def test_mcp_connection(url: str, key: str | None):
    headers = {
        **auth_headers(key),
        "Accept": "application/json, text/event-stream",
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                url,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": "agenttrace-connection-test",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "TraceMend", "version": "0.2.0"},
                    },
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="无法连接 MCP 服务，请检查地址和网络") from exc
    if not response.is_success:
        raise HTTPException(
            status_code=502, detail=f"MCP 服务返回 HTTP {response.status_code}"
        )
    try:
        if "text/event-stream" in response.headers.get("content-type", ""):
            data = next(
                line[5:].strip()
                for line in response.text.splitlines()
                if line.startswith("data:")
            )
            output = json.loads(data)
        else:
            output = response.json()
    except (ValueError, StopIteration) as exc:
        raise HTTPException(status_code=502, detail="MCP 服务未返回有效 JSON-RPC 响应") from exc
    if isinstance(output, dict) and output.get("error"):
        raise HTTPException(status_code=502, detail="MCP initialize 返回协议错误")


@router.post("/test")
async def test_connection(payload: SettingsTestIn, request: Request):
    settings = request.app.state.settings
    if payload.kind == "mcp":
        if not settings.default_mcp_upstream:
            raise HTTPException(status_code=409, detail="请先填写并保存 MCP 服务地址")
        await test_mcp_connection(settings.default_mcp_upstream, settings.default_mcp_key)
        return {"ok": True, "kind": "mcp", "message": "MCP initialize 连接成功"}
    if payload.kind == "check":
        if not settings.check_model_key:
            raise HTTPException(status_code=409, detail="请先填写并保存检测模型密钥")
        base, model, key, timeout = (
            settings.check_model_base,
            settings.check_model_name,
            settings.check_model_key,
            settings.check_model_timeout,
        )
    else:
        if not settings.default_upstream_model:
            raise HTTPException(status_code=409, detail="请先填写并保存默认模型名称")
        base, model, key, timeout = (
            settings.default_upstream_base,
            settings.default_upstream_model,
            settings.default_upstream_key,
            30.0,
        )
    await test_openai_connection(base, model, key, timeout)
    return {"ok": True, "kind": payload.kind, "message": "模型连接成功", "model": model}
