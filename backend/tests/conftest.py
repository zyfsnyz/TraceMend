from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def application(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.delenv("ADMIN_KEY", raising=False)
    from app.settings_store import ENV_KEY_BY_FIELD

    for key in ENV_KEY_BY_FIELD.values():
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("app.config.PROJECT_ENV", tmp_path / ".env")
    monkeypatch.setattr("app.config.BOOTSTRAP_ENV", tmp_path / ".env")
    from app.main import create_app

    return create_app()


@pytest_asyncio.fixture
async def client(application) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://testserver"
    ) as test_client:
        yield test_client
