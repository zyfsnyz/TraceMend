import pytest


@pytest.mark.asyncio
async def test_health_reports_service_and_database(client):
    response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database"] == "ok"
    assert response.json()["version"]
