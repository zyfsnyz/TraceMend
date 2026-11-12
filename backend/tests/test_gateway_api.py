import pytest


@pytest.mark.asyncio
async def test_gateway_route_crud_never_exposes_api_key(client, application):
    created = await client.post(
        "/api/gateway/routes",
        json={
            "name": "DeepSeek 主路由",
            "match_model": "deepseek-*",
            "target_model": "deepseek-chat",
            "upstream_base": "https://api.deepseek.com/v1",
            "api_key": "route-secret",
            "priority": 10,
            "enabled": True,
            "input_price_per_million": 0.5,
            "output_price_per_million": 1.5,
        },
    )
    assert created.status_code == 201
    route = created.json()
    assert route["api_key_configured"] is True
    assert route["circuit"]["status"] == "closed"
    assert "route-secret" not in created.text
    assert "api_key" not in route

    listed = await client.get("/api/gateway/routes")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["name"] == "DeepSeek 主路由"
    assert "route-secret" not in listed.text

    updated = await client.patch(
        f"/api/gateway/routes/{route['id']}", json={"enabled": False, "priority": 20}
    )
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False
    assert updated.json()["api_key_configured"] is True

    deleted = await client.delete(f"/api/gateway/routes/{route['id']}")
    assert deleted.status_code == 204
    assert (await client.get("/api/gateway/routes")).json()["items"] == []


@pytest.mark.asyncio
async def test_gateway_route_validation_and_missing_route(client):
    invalid = await client.post(
        "/api/gateway/routes",
        json={
            "name": "bad",
            "match_model": "*",
            "target_model": "x",
            "upstream_base": "ftp://example.com/v1",
        },
    )
    assert invalid.status_code == 422
    missing = await client.patch("/api/gateway/routes/not-found", json={"enabled": False})
    assert missing.status_code == 404
