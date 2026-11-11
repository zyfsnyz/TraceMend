import pytest
from httpx import ASGITransport, AsyncClient


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
