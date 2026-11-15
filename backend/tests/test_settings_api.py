import os
import re
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit

import pytest

from app.settings_store import public_settings

pytest_plugins = ["upstream_fixtures", "mcp_upstream_fixture"]

SENSITIVE_QUERY_KEYS = [
    "api_key",
    "APIKEY",
    "Key",
    "TOKEN",
    "access_token",
    "Authorization",
    "auth",
    "SECRET",
    "Password",
    "credential",
    "client_secret",
    "refresh_token",
    "api-key",
    "sig",
    "signature",
    "X-Amz-Signature",
    "X-Goog-Credential",
]


@pytest.mark.asyncio
async def test_settings_round_trip_never_returns_secrets_and_reloads_runtime(
    client, application
):
    response = await client.put(
        "/api/settings",
        json={
            "default_upstream_base": "https://api.deepseek.com/v1",
            "default_upstream_model": "deepseek-chat",
            "default_upstream_key": "default-secret",
            "default_mcp_upstream": "http://127.0.0.1:9000/mcp",
            "default_mcp_key": "mcp-secret",
            "enable_llm_check": True,
            "check_model_base": "https://apihub.agnes-ai.com/v1",
            "check_model_name": "agnes-3.0-flash",
            "check_model_key": "check-secret",
            "check_model_timeout": 45,
            "gateway_daily_token_budget": 50000,
            "gateway_failure_threshold": 3,
            "gateway_cooldown_seconds": 20,
            "default_input_price_per_million": 0.5,
            "default_output_price_per_million": 1.5,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["default_upstream_model"] == "deepseek-chat"
    assert body["default_upstream_key_configured"] is True
    assert body["default_mcp_key_configured"] is True
    assert body["check_model_key_configured"] is True
    assert body["gateway_daily_token_budget"] == 50000
    assert "default-secret" not in response.text
    assert "mcp-secret" not in response.text
    assert "check-secret" not in response.text
    assert "default_upstream_key" not in body
    assert "default_mcp_key" not in body
    assert "check_model_key" not in body
    assert application.state.settings.default_upstream_model == "deepseek-chat"
    assert application.state.settings.check_model_key == "check-secret"

    env_text = application.state.env_path.read_text(encoding="utf-8")
    assert "DEFAULT_UPSTREAM_MODEL" in env_text
    assert "CHECK_MODEL_KEY" in env_text

    fetched = await client.get("/api/settings")
    assert fetched.status_code == 200
    assert fetched.json() == body


@pytest.mark.asyncio
async def test_settings_blank_secret_clears_and_omitted_secret_is_unchanged(
    client, application
):
    await client.put("/api/settings", json={"check_model_key": "keep-me"})
    await client.put("/api/settings", json={"check_model_name": "new-model"})
    assert application.state.settings.check_model_key == "keep-me"
    response = await client.put("/api/settings", json={"check_model_key": ""})
    assert response.status_code == 200
    assert response.json()["check_model_key_configured"] is False
    assert not application.state.settings.check_model_key


@pytest.mark.asyncio
async def test_settings_test_openai_uses_saved_server_side_key(
    client, openai_upstream
):
    saved = await client.put(
        "/api/settings",
        json={
            "default_upstream_base": openai_upstream.base_url,
            "default_upstream_model": "connection-test",
            "default_upstream_key": "server-side-secret",
        },
    )
    assert saved.status_code == 200
    response = await client.post("/api/settings/test", json={"kind": "openai"})
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert openai_upstream.requests[-1]["payload"]["model"] == "connection-test"
    assert (
        openai_upstream.requests[-1]["headers"]["authorization"]
        == "Bearer server-side-secret"
    )
    assert "server-side-secret" not in response.text


@pytest.mark.asyncio
async def test_settings_rejects_invalid_upstream_url(client):
    response = await client.put(
        "/api/settings", json={"default_upstream_base": "file:///etc/passwd"}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_env_update_preserves_unrelated_lines(application):
    from app.settings_store import update_env_file

    path = application.state.env_path
    path.write_text("# keep this\nCUSTOM_VALUE=hello\n", encoding="utf-8")
    update_env_file(path, {"CHECK_MODEL_NAME": "model with space"})
    text = path.read_text(encoding="utf-8")
    assert "# keep this" in text
    assert "CUSTOM_VALUE=hello" in text
    assert 'CHECK_MODEL_NAME="model with space"' in text


@pytest.mark.asyncio
async def test_saved_mcp_url_and_key_are_used_without_client_headers(client, mcp_upstream):
    await client.put(
        "/api/settings",
        json={"default_mcp_upstream": mcp_upstream.url, "default_mcp_key": "mcp-saved-secret"},
    )
    response = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers={"X-AgentTrace-Id": "mcp-web-config"},
    )
    assert response.status_code == 200
    assert mcp_upstream.requests[-1]["headers"]["authorization"] == "Bearer mcp-saved-secret"
    detail = await client.get("/api/traces/mcp-web-config")
    assert detail.status_code == 200
    assert "mcp-saved-secret" not in detail.text


def test_persistent_env_overrides_bootstrap_without_exposing_secrets(tmp_path, monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("CHECK_MODEL_NAME", raising=False)
    monkeypatch.delenv("CHECK_MODEL_KEY", raising=False)
    bootstrap = tmp_path / "bootstrap.env"
    persistent = tmp_path / ".env"
    bootstrap.write_text(
        "CHECK_MODEL_NAME=bootstrap-model\nCHECK_MODEL_KEY=bootstrap-secret\n",
        encoding="utf-8",
    )
    persistent.write_text("CHECK_MODEL_NAME=web-model\n", encoding="utf-8")
    settings = Settings(_env_file=(bootstrap, persistent))
    assert settings.check_model_name == "web-model"
    assert settings.check_model_key == "bootstrap-secret"
    assert "bootstrap-secret" not in str(public_settings(settings))


@pytest.mark.asyncio
async def test_explicit_mcp_upstream_never_receives_saved_default_key(client, mcp_upstream):
    await client.put("/api/settings", json={"default_mcp_key": "mcp-must-not-leak"})
    response = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers={"X-MCP-Upstream": mcp_upstream.url},
    )
    assert response.status_code == 200
    assert "authorization" not in mcp_upstream.requests[-1]["headers"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "default_upstream_base",
        "default_upstream_model",
        "default_mcp_upstream",
        "enable_llm_check",
        "check_model_base",
        "check_model_name",
        "check_model_timeout",
        "gateway_daily_token_budget",
        "gateway_failure_threshold",
        "gateway_cooldown_seconds",
        "default_input_price_per_million",
        "default_output_price_per_million",
    ],
)
async def test_settings_patch_rejects_explicit_null_for_non_secret_fields(client, field):
    response = await client.put("/api/settings", json={field: None})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_settings_null_secrets_keep_values_and_env_file_unchanged(client, application):
    secrets = {
        "default_upstream_key": "default-null-secret",
        "default_mcp_key": "mcp-null-secret",
        "check_model_key": "check-null-secret",
    }
    saved = await client.put("/api/settings", json=secrets)
    assert saved.status_code == 200
    env_before = application.state.env_path.read_bytes()

    response = await client.put(
        "/api/settings",
        json={field: None for field in secrets},
    )

    assert response.status_code == 200
    assert response.json()["default_upstream_key_configured"] is True
    assert response.json()["default_mcp_key_configured"] is True
    assert response.json()["check_model_key_configured"] is True
    assert application.state.env_path.read_bytes() == env_before
    assert application.state.settings.default_upstream_key == "default-null-secret"
    assert application.state.settings.default_mcp_key == "mcp-null-secret"
    assert application.state.settings.check_model_key == "check-null-secret"


@pytest.mark.asyncio
async def test_invalid_candidate_settings_do_not_partially_update_any_state(
    client, application
):
    from app.settings_store import save_settings

    saved = await client.put("/api/settings", json={"check_model_timeout": 30})
    assert saved.status_code == 200
    env_before = application.state.env_path.read_bytes()
    environ_before = os.environ.get("CHECK_MODEL_TIMEOUT")
    settings_before = application.state.settings

    with pytest.raises(ValueError):
        save_settings(application, {"check_model_timeout": "not-a-number"})

    assert application.state.env_path.read_bytes() == env_before
    assert os.environ.get("CHECK_MODEL_TIMEOUT") == environ_before
    assert application.state.settings is settings_before
    assert application.state.settings.check_model_timeout == 30


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "name",
        "match_model",
        "target_model",
        "upstream_base",
        "priority",
        "enabled",
        "input_price_per_million",
        "output_price_per_million",
    ],
)
async def test_route_patch_rejects_explicit_null_for_non_secret_fields(client, field):
    created = await client.post(
        "/api/gateway/routes",
        json={
            "name": "null 校验路由",
            "match_model": "null-check-*",
            "target_model": "null-check-model",
            "upstream_base": "https://example.com/v1",
        },
    )
    assert created.status_code == 201

    response = await client.patch(
        f"/api/gateway/routes/{created.json()['id']}", json={field: None}
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_route_null_key_is_unchanged_and_blank_key_clears(client, application):
    created = await client.post(
        "/api/gateway/routes",
        json={
            "name": "密钥更新路由",
            "match_model": "secret-*",
            "target_model": "secret-model",
            "upstream_base": "https://example.com/v1",
            "api_key": "route-null-secret",
        },
    )
    assert created.status_code == 201
    route_id = created.json()["id"]
    env_before = application.state.env_path.read_bytes()

    unchanged = await client.patch(f"/api/gateway/routes/{route_id}", json={"api_key": None})

    assert unchanged.status_code == 200
    assert unchanged.json()["api_key_configured"] is True
    assert application.state.env_path.read_bytes() == env_before

    cleared = await client.patch(f"/api/gateway/routes/{route_id}", json={"api_key": ""})

    assert cleared.status_code == 200
    assert cleared.json()["api_key_configured"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["default_upstream_base", "check_model_base"])
@pytest.mark.parametrize("suffix", ["?region=cn", "#legacy-fragment"])
async def test_openai_base_urls_reject_query_and_fragment(client, field, suffix):
    response = await client.put(
        "/api/settings", json={field: f"https://example.com/v1{suffix}"}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["?region=cn", "#legacy-fragment"])
async def test_route_upstream_base_rejects_query_and_fragment(client, suffix):
    payload = {
        "name": "URL 校验路由",
        "match_model": "url-*",
        "target_model": "url-model",
        "upstream_base": f"https://example.com/v1{suffix}",
    }
    created = await client.post("/api/gateway/routes", json=payload)
    assert created.status_code == 422

    payload["upstream_base"] = "https://example.com/v1"
    created = await client.post("/api/gateway/routes", json=payload)
    assert created.status_code == 201
    updated = await client.patch(
        f"/api/gateway/routes/{created.json()['id']}",
        json={"upstream_base": f"https://example.com/v1{suffix}"},
    )
    assert updated.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "suffix",
    [*(f"?{key}=credential-value" for key in SENSITIVE_QUERY_KEYS), "#mcp-fragment"],
)
async def test_mcp_url_rejects_credential_query_parameters_and_fragment(client, suffix):
    response = await client.put(
        "/api/settings",
        json={"default_mcp_upstream": f"https://example.com/mcp{suffix}"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_mcp_url_preserves_non_sensitive_query(client):
    url = "https://example.com/mcp?tenant=cn&mode=stream"
    response = await client.put("/api/settings", json={"default_mcp_upstream": url})
    assert response.status_code == 200
    assert response.json()["default_mcp_upstream"] == url


@pytest.mark.asyncio
async def test_public_urls_redact_all_legacy_credential_query_values(client, application):
    from app.models import GatewayRoute

    sensitive_values = {key: f"legacy-{key}-value" for key in SENSITIVE_QUERY_KEYS}
    query = urlencode({**sensitive_values, "tenant": "cn"})
    application.state.settings = application.state.settings.model_copy(
        update={
            "default_upstream_base": f"https://legacy.example.com/v1?{query}",
            "default_mcp_upstream": f"https://legacy.example.com/mcp?{query}",
            "check_model_base": f"https://legacy.example.com/check/v1?{query}",
        }
    )
    with application.state.session_factory() as session:
        session.add(
            GatewayRoute(
                id="legacy-sensitive-route",
                name="legacy 敏感路由",
                match_model="legacy-*",
                target_model="legacy-model",
                upstream_base=f"https://legacy.example.com/route/v1?{query}",
            )
        )
        session.commit()

    settings_response = await client.get("/api/settings")
    routes_response = await client.get("/api/gateway/routes")

    assert settings_response.status_code == 200
    assert routes_response.status_code == 200
    combined_output = settings_response.text + routes_response.text
    for secret_value in sensitive_values.values():
        assert secret_value not in combined_output

    public_urls = [
        settings_response.json()[field]
        for field in ("default_upstream_base", "default_mcp_upstream", "check_model_base")
    ]
    public_urls.append(routes_response.json()["items"][0]["upstream_base"])
    for public_url in public_urls:
        public_query = dict(parse_qsl(urlsplit(public_url).query, keep_blank_values=True))
        assert public_query["tenant"] == "cn"
        for key, secret_value in sensitive_values.items():
            assert public_query[key] != secret_value


@pytest.mark.asyncio
async def test_public_urls_mask_unrecognized_query_values_by_default(client, application):
    application.state.settings = application.state.settings.model_copy(
        update={
            "default_mcp_upstream": (
                "https://legacy.example.com/mcp?tenant=cn&custom=unrecognized-secret"
            )
        }
    )

    response = await client.get("/api/settings")

    assert response.status_code == 200
    assert "unrecognized-secret" not in response.text
    query = dict(
        parse_qsl(
            urlsplit(response.json()["default_mcp_upstream"]).query,
            keep_blank_values=True,
        )
    )
    assert query == {"tenant": "cn", "custom": "REDACTED"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload", "secrets"),
    [
        (
            "PUT",
            "/api/settings",
            {"default_upstream_key": "settings-validation-secret", "default_upstream_model": None},
            ["settings-validation-secret"],
        ),
        (
            "POST",
            "/api/gateway/routes",
            {
                "name": "invalid URL",
                "match_model": "demo-*",
                "target_model": "demo",
                "upstream_base": "https://user:url-validation-secret@example.com/v1",
                "api_key": "route-create-validation-secret",
            },
            ["url-validation-secret", "route-create-validation-secret"],
        ),
        (
            "PATCH",
            "/api/gateway/routes/not-yet-created",
            {"api_key": "route-patch-validation-secret", "name": None},
            ["route-patch-validation-secret"],
        ),
    ],
)
async def test_validation_errors_never_echo_sensitive_input(
    client, method, path, payload, secrets
):
    response = await client.request(method, path, json=payload)

    assert response.status_code == 422
    for secret in secrets:
        assert secret not in response.text
    assert response.json()["detail"]
    for error in response.json()["detail"]:
        assert "input" not in error
        assert "ctx" not in error


def test_env_update_removes_later_duplicates_and_restart_reads_latest(
    application, monkeypatch
):
    from app.config import Settings
    from app.settings_store import update_env_file

    path = application.state.env_path
    path.write_text(
        "CHECK_MODEL_NAME=old-first\nCUSTOM_VALUE=keep\nCHECK_MODEL_NAME=old-last\n",
        encoding="utf-8",
    )

    update_env_file(path, {"CHECK_MODEL_NAME": "latest-model"})

    lines = path.read_text(encoding="utf-8").splitlines()
    assert sum(line.startswith("CHECK_MODEL_NAME=") for line in lines) == 1
    assert "CUSTOM_VALUE=keep" in lines
    monkeypatch.delenv("CHECK_MODEL_NAME", raising=False)
    restarted = Settings(_env_file=path)
    assert restarted.check_model_name == "latest-model"


@pytest.mark.asyncio
async def test_guide_placeholder_auth_only_uses_saved_key_for_default_upstream(
    client, openai_upstream
):
    saved = await client.put(
        "/api/settings",
        json={
            "default_upstream_base": openai_upstream.base_url,
            "default_upstream_model": "guide-model",
            "default_upstream_key": "guide-server-secret",
        },
    )
    assert saved.status_code == 200
    payload = {"model": "guide-model", "messages": [{"role": "user", "content": "你好"}]}

    placeholder = await client.post(
        "/v1/chat/completions",
        json=payload,
        headers={"Authorization": "Bearer agenttrace"},
    )
    assert placeholder.status_code == 200
    assert (
        openai_upstream.requests[-1]["headers"]["authorization"]
        == "Bearer guide-server-secret"
    )

    real_client = await client.post(
        "/v1/chat/completions",
        json=payload,
        headers={"Authorization": "Bearer real-client-secret"},
    )
    assert real_client.status_code == 200
    assert (
        openai_upstream.requests[-1]["headers"]["authorization"]
        == "Bearer real-client-secret"
    )

    explicit = await client.post(
        "/v1/chat/completions",
        json=payload,
        headers={
            "Authorization": "Bearer agenttrace",
            "X-Upstream-Base": openai_upstream.base_url,
        },
    )
    assert explicit.status_code == 200
    assert openai_upstream.requests[-1]["headers"]["authorization"] == "Bearer agenttrace"


@pytest.mark.asyncio
async def test_public_urls_strip_userinfo_and_fragment_even_for_legacy_values(
    client, application
):
    from app.models import GatewayRoute

    application.state.settings = application.state.settings.model_copy(
        update={
            "default_upstream_base": (
                "https://upstream-user:upstream-pass@legacy.example.com/v1"
                "?tenant=cn&api_key=upstream-secret#upstream-frag"
            ),
            "default_mcp_upstream": (
                "https://mcp-user:mcp-pass@legacy.example.com/mcp"
                "?tenant=cn&token=mcp-secret#mcp-frag"
            ),
            "check_model_base": (
                "https://check-user:check-pass@legacy.example.com/check/v1"
                "?tenant=cn#check-frag"
            ),
        }
    )
    with application.state.session_factory() as session:
        session.add(
            GatewayRoute(
                id="legacy-userinfo-route",
                name="legacy userinfo 路由",
                match_model="legacy-userinfo-*",
                target_model="legacy-userinfo-model",
                upstream_base=(
                    "https://route-user:route-pass@legacy.example.com/route/v1"
                    "?tenant=cn&access_token=route-secret#route-frag"
                ),
            )
        )
        session.commit()

    settings_response = await client.get("/api/settings")
    routes_response = await client.get("/api/gateway/routes")

    assert settings_response.status_code == 200
    assert routes_response.status_code == 200
    combined_output = settings_response.text + routes_response.text
    for leaked in (
        "upstream-pass",
        "upstream-secret",
        "mcp-pass",
        "mcp-secret",
        "check-pass",
        "route-user",
        "route-pass",
        "route-secret",
        "upstream-frag",
        "mcp-frag",
        "check-frag",
        "route-frag",
    ):
        assert leaked not in combined_output

    public_urls = [
        settings_response.json()["default_upstream_base"],
        settings_response.json()["default_mcp_upstream"],
        settings_response.json()["check_model_base"],
        routes_response.json()["items"][0]["upstream_base"],
    ]
    expected_query = {
        "default_upstream_base": {"tenant": "cn", "api_key": "REDACTED"},
        "default_mcp_upstream": {"tenant": "cn", "token": "REDACTED"},
        "check_model_base": {"tenant": "cn"},
        "route": {"tenant": "cn", "access_token": "REDACTED"},
    }
    for label, public_url in zip(
        ("default_upstream_base", "default_mcp_upstream", "check_model_base", "route"),
        public_urls,
        strict=True,
    ):
        parsed = urlsplit(public_url)
        assert "@" not in parsed.netloc
        assert parsed.username is None
        assert parsed.password is None
        assert parsed.fragment == ""
        public_query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        assert public_query == expected_query[label]


@pytest.mark.asyncio
async def test_new_settings_and_routes_reject_userinfo_and_fragment(client):
    settings_userinfo = await client.put(
        "/api/settings",
        json={"check_model_base": "https://user:pass@example.com/v1"},
    )
    assert settings_userinfo.status_code == 422
    mcp_userinfo = await client.put(
        "/api/settings",
        json={"default_mcp_upstream": "https://user:pass@example.com/mcp"},
    )
    assert mcp_userinfo.status_code == 422
    settings_fragment = await client.put(
        "/api/settings", json={"default_upstream_base": "https://example.com/v1#frag"}
    )
    assert settings_fragment.status_code == 422
    payload = {
        "name": "凭据拒绝路由",
        "match_model": "userinfo-*",
        "target_model": "userinfo-model",
        "upstream_base": "https://user:pass@example.com/v1",
    }
    assert (await client.post("/api/gateway/routes", json=payload)).status_code == 422
    payload["upstream_base"] = "https://example.com/v1#frag"
    assert (await client.post("/api/gateway/routes", json=payload)).status_code == 422


@pytest.mark.asyncio
async def test_concurrent_settings_saves_keep_both_updates(application, monkeypatch):
    from app import settings_store
    from app.config import Settings as RealSettings

    class SlowSettings(RealSettings):
        def __init__(self, **kwargs):
            time.sleep(0.05)
            super().__init__(**kwargs)

    monkeypatch.setattr(settings_store, "Settings", SlowSettings)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def save(values: dict[str, object]) -> None:
        try:
            barrier.wait(timeout=5)
            settings_store.save_settings(application, values)
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    threads = [
        threading.Thread(target=save, args=({"check_model_name": "concurrent-model"},)),
        threading.Thread(target=save, args=({"gateway_failure_threshold": 7},)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert application.state.settings.check_model_name == "concurrent-model"
    assert application.state.settings.gateway_failure_threshold == 7

    env_text = application.state.env_path.read_text(encoding="utf-8")
    assert "concurrent-model" in env_text
    assert re.search(r"^GATEWAY_FAILURE_THRESHOLD=\"?7\"?$", env_text, re.MULTILINE)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "initial",
    [
        "CHECK_MODEL_NAME=old-first\nCUSTOM_VALUE=keep\nexport CHECK_MODEL_NAME=old-export\n",
        "export CHECK_MODEL_NAME=old-first\nCUSTOM_VALUE=keep\nCHECK_MODEL_NAME=old-second\n",
    ],
)
async def test_env_update_collapses_export_prefixed_duplicates(
    application, monkeypatch, initial
):
    from app.config import Settings
    from app.settings_store import update_env_file

    path = application.state.env_path
    path.write_text(initial, encoding="utf-8")

    update_env_file(path, {"CHECK_MODEL_NAME": "latest-model"})

    lines = path.read_text(encoding="utf-8").splitlines()
    assignments = [
        line
        for line in lines
        if re.match(r"^\s*(?:export\s+)?CHECK_MODEL_NAME\s*=", line)
    ]
    assert len(assignments) == 1
    assert "CUSTOM_VALUE=keep" in lines
    monkeypatch.delenv("CHECK_MODEL_NAME", raising=False)
    restarted = Settings(_env_file=path)
    assert restarted.check_model_name == "latest-model"
