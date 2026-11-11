import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_sqlite_parent_directory_is_created_on_first_start(tmp_path, monkeypatch):
    database = tmp_path / "nested" / "data" / "agenttrace.db"
    monkeypatch.setenv("DB_URL", f"sqlite:///{database}")
    from app.main import create_app

    application = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://testserver"
    ) as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert database.exists()
