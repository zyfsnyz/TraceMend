import pytest
from httpx import ASGITransport, AsyncClient


def test_admin_key_comparison_is_safe_for_non_ascii_candidates():
    from app.main import admin_key_matches

    assert admin_key_matches("local-admin", "local-admin") is True
    assert admin_key_matches("wrong", "local-admin") is False
    assert admin_key_matches("é", "local-admin") is False
    assert admin_key_matches("", "local-admin") is False


def test_settings_reject_non_ascii_admin_key():
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError, match="ADMIN_KEY 只能使用 ASCII"):
        Settings(admin_key="测试密钥")


@pytest.mark.parametrize(
    "key",
    [
        "api_token",
        "apiToken",
        "API-TOKEN",
        "accessKeyId",
        "access_key_id",
        "authKey",
        "sessionKey",
        "session_key",
        "x-api-token",
        "X-Api-Token",
        "user_token",
        "max_output_token",
        "signing_key",
        "signing_key_id",
        "oauth_auth",
        "client_auth",
    ],
)
def test_replay_credential_key_filter_covers_remaining_bypasses(key):
    from app.replay import _credential_like_key

    assert _credential_like_key(key) is True


@pytest.mark.parametrize(
    "key",
    [
        "model",
        "messages",
        "max_tokens",
        "max_completion_tokens",
        "top_p",
        "temperature",
        "stop",
        "stream",
        "stream_options",
        "tools",
        "tool_choice",
        "response_format",
        "presence_penalty",
        "frequency_penalty",
        "logprobs",
        "top_logprobs",
        "seed",
        "user",
    ],
)
def test_replay_credential_key_filter_keeps_legitimate_parameters(key):
    from app.replay import _credential_like_key

    assert _credential_like_key(key) is False


@pytest.mark.asyncio
async def test_non_ascii_admin_header_is_rejected_without_server_error(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'header.db'}")
    monkeypatch.setenv("ADMIN_KEY", "local-admin")
    from app.main import create_app

    application = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/traces", headers=[(b"x-admin-key", b"\xe9")]
        )
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_admin_key_protects_api_except_health_and_cors_allows_frontend(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'secure.db'}")
    monkeypatch.setenv("ADMIN_KEY", "local-admin")
    from app.main import create_app

    application = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://testserver"
    ) as client:
        assert (await client.get("/api/health")).status_code == 200
        assert (await client.get("/api/traces")).status_code == 401
        assert (
            await client.get("/api/traces", headers={"X-Admin-Key": "wrong"})
        ).status_code == 401
        assert (
            await client.get("/api/traces", headers={"X-Admin-Key": "local-admin"})
        ).status_code == 200
        preflight = await client.options(
            "/api/traces",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "http://localhost:3000"
