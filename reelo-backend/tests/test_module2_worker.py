"""worker.tasks.produce_episode wiring: builds ctx, calls runner, flushes usage."""

from __future__ import annotations

import pytest

import worker.tasks as tasks
from clients.base import CallContext
from keystore import Cipher, KeyStore
from usage import UsageLogger


async def test_produce_episode_calls_runner_and_flushes(monkeypatch):
    flushed: list[bool] = []
    seen: dict = {}

    async def fake_build_ctx(ctx, user_id):
        return CallContext(user_id=user_id, keys=KeyStore(Cipher(b"k" * 32)), usage=UsageLogger())

    async def fake_flush(call_ctx):
        flushed.append(True)
        return 0

    async def fake_run(user_id, episode_id, ctx, **kw):
        seen["args"] = (user_id, episode_id)
        return {"episode_id": episode_id, "status": "assembled", "images": 3}

    monkeypatch.setattr(tasks, "build_call_context", fake_build_ctx)
    monkeypatch.setattr(tasks, "flush_call_context_usage", fake_flush)

    import module2.runner as runner

    monkeypatch.setattr(runner, "run_produce_episode", fake_run)

    result = await tasks.produce_episode({}, "u1", "e1")

    assert result["status"] == "assembled"
    assert seen["args"] == ("u1", "e1")
    assert flushed == [True]


async def test_produce_episode_flushes_on_error(monkeypatch):
    flushed: list[bool] = []

    async def fake_build_ctx(ctx, user_id):
        return CallContext(user_id=user_id, keys=KeyStore(Cipher(b"k" * 32)), usage=UsageLogger())

    async def fake_flush(call_ctx):
        flushed.append(True)
        return 0

    async def fake_run(user_id, episode_id, ctx, **kw):
        raise RuntimeError("image failed; render blocked")

    monkeypatch.setattr(tasks, "build_call_context", fake_build_ctx)
    monkeypatch.setattr(tasks, "flush_call_context_usage", fake_flush)

    import module2.runner as runner

    monkeypatch.setattr(runner, "run_produce_episode", fake_run)

    with pytest.raises(RuntimeError):
        await tasks.produce_episode({}, "u1", "e1")
    assert flushed == [True]  # flush still runs in finally
