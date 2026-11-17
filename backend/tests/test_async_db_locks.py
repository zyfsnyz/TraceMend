import asyncio
import threading
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_replay import configure_default_upstream, post_replay, seed_source

pytest_plugins = ["upstream_fixtures"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["openai", "mcp", "llm-check"])
async def test_async_replay_guards_do_not_block_event_loop_on_sqlite_lock(
    client, application, controlled_openai_upstream, openai_upstream, mode
):
    source_trace_id = f"replay-async-lock-{mode}"
    replay_trace_id = str(uuid4())
    await seed_source(
        client,
        source_trace_id,
        span_input={
            "model": "safe-model",
            "messages": [{"role": "user", "content": "写锁等待不能冻结事件循环"}],
        },
    )
    await configure_default_upstream(client, controlled_openai_upstream)
    settings = application.state.settings
    settings.enable_llm_check = True
    settings.check_model_key = "synthetic-test-only"
    settings.check_model_name = "synthetic-check-model"
    controlled_openai_upstream.release.clear()
    replay_task = asyncio.create_task(
        post_replay(client, source_trace_id, idempotency_key=replay_trace_id)
    )
    arrived = await asyncio.to_thread(controlled_openai_upstream.request_arrived.wait, 5)
    assert arrived

    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def hold_write_lock():
        with application.state.session_factory() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            lock_acquired.set()
            if not release_lock.wait(5):
                raise RuntimeError("timed out waiting to release external write lock")
            session.rollback()

    lock_thread = threading.Thread(target=hold_write_lock, daemon=True)
    lock_thread.start()
    acquired = await asyncio.to_thread(lock_acquired.wait, 5)
    assert acquired
    stop_heartbeat = False
    maximum_lag = 0.0

    async def heartbeat():
        nonlocal maximum_lag
        previous = asyncio.get_running_loop().time()
        while not stop_heartbeat:
            await asyncio.sleep(0.02)
            now = asyncio.get_running_loop().time()
            maximum_lag = max(maximum_lag, now - previous - 0.02)
            previous = now

    heartbeat_task = asyncio.create_task(heartbeat())
    release_timer = threading.Timer(0.6, release_lock.set)
    release_timer.start()
    try:
        if mode == "openai":
            response = await client.post(
                "/v1/chat/completions",
                headers={
                    "X-AgentTrace-Id": replay_trace_id,
                    "X-Upstream-Base": openai_upstream.base_url,
                },
                json={"model": "safe-model", "messages": []},
            )
        elif mode == "mcp":
            response = await client.post(
                "/mcp",
                headers={
                    "X-AgentTrace-Id": replay_trace_id,
                    "X-MCP-Upstream": openai_upstream.base_url + "/chat/completions",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            )
        else:
            response = await client.post(f"/api/traces/{replay_trace_id}/llm-check")
    finally:
        release_lock.set()
        release_timer.cancel()
        stop_heartbeat = True
        await heartbeat_task
        controlled_openai_upstream.release.set()
        replayed = await replay_task
        await asyncio.to_thread(lock_thread.join, 5)

    assert response.status_code == 409
    assert maximum_lag < 0.3
    assert openai_upstream.requests == []
    assert replayed.status_code == 201
    assert len(controlled_openai_upstream.requests) == 1


async def start_running_replay(client, controlled_openai_upstream, suffix: str):
    source_trace_id = f"replay-async-mutation-{suffix}"
    replay_trace_id = str(uuid4())
    await seed_source(
        client,
        source_trace_id,
        span_input={
            "model": "safe-model",
            "messages": [{"role": "user", "content": "变更接口写锁心跳"}],
        },
    )
    await configure_default_upstream(client, controlled_openai_upstream)
    controlled_openai_upstream.release.clear()
    replay_task = asyncio.create_task(
        post_replay(client, source_trace_id, idempotency_key=replay_trace_id)
    )
    arrived = await asyncio.to_thread(controlled_openai_upstream.request_arrived.wait, 5)
    assert arrived
    return replay_trace_id, replay_task


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["reanalyze", "delete"])
async def test_async_trace_mutations_do_not_block_on_writer_lock(
    client, application, controlled_openai_upstream, path
):
    replay_trace_id, replay_task = await start_running_replay(
        client, controlled_openai_upstream, path
    )
    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def hold_write_lock():
        with application.state.session_factory() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            lock_acquired.set()
            if not release_lock.wait(5):
                raise RuntimeError("timed out waiting to release external write lock")
            session.rollback()

    lock_thread = threading.Thread(target=hold_write_lock, daemon=True)
    lock_thread.start()
    assert await asyncio.to_thread(lock_acquired.wait, 5)
    stop_heartbeat = False
    maximum_lag = 0.0

    async def heartbeat():
        nonlocal maximum_lag
        previous = asyncio.get_running_loop().time()
        while not stop_heartbeat:
            await asyncio.sleep(0.02)
            now = asyncio.get_running_loop().time()
            maximum_lag = max(maximum_lag, now - previous - 0.02)
            previous = now

    heartbeat_task = asyncio.create_task(heartbeat())
    release_timer = threading.Timer(0.6, release_lock.set)
    release_timer.start()
    try:
        method = client.delete if path == "delete" else client.post
        url = (
            f"/api/traces/{replay_trace_id}"
            if path == "delete"
            else f"/api/traces/{replay_trace_id}/{path}"
        )
        response = await method(url)
    finally:
        release_lock.set()
        release_timer.cancel()
        stop_heartbeat = True
        await heartbeat_task
        controlled_openai_upstream.release.set()
        replayed = await replay_task
        await asyncio.to_thread(lock_thread.join, 5)

    assert response.status_code == 409, {
        "body": response.text,
        "replay_status": replayed.status_code,
        "maximum_lag": maximum_lag,
    }
    assert response.json()["detail"] == "重放执行期间禁止修改或删除其审计链路"
    assert maximum_lag < 0.3
    assert replayed.status_code == 201


@pytest.mark.asyncio
async def test_replay_reservation_lock_wait_does_not_block_event_loop(
    client, application, openai_upstream
):
    source_trace_id = "replay-async-reservation-lock"
    await seed_source(
        client,
        source_trace_id,
        span_input={
            "model": "safe-model",
            "messages": [{"role": "user", "content": "预留写锁心跳"}],
        },
    )
    await configure_default_upstream(client, openai_upstream)
    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def hold_write_lock():
        with application.state.session_factory() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            lock_acquired.set()
            if not release_lock.wait(5):
                raise RuntimeError("timed out waiting to release external write lock")
            session.rollback()

    lock_thread = threading.Thread(target=hold_write_lock, daemon=True)
    lock_thread.start()
    assert await asyncio.to_thread(lock_acquired.wait, 5)
    stop_heartbeat = False
    maximum_lag = 0.0

    async def heartbeat():
        nonlocal maximum_lag
        previous = asyncio.get_running_loop().time()
        while not stop_heartbeat:
            await asyncio.sleep(0.02)
            now = asyncio.get_running_loop().time()
            maximum_lag = max(maximum_lag, now - previous - 0.02)
            previous = now

    heartbeat_task = asyncio.create_task(heartbeat())
    release_timer = threading.Timer(0.6, release_lock.set)
    release_timer.start()
    try:
        response = await post_replay(client, source_trace_id)
    finally:
        release_lock.set()
        release_timer.cancel()
        stop_heartbeat = True
        await heartbeat_task
        await asyncio.to_thread(lock_thread.join, 5)

    assert response.status_code == 201
    assert maximum_lag < 0.3
    assert len(openai_upstream.requests) == 1
