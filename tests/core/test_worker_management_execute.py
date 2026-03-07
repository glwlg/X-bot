import importlib.util
import sys
from pathlib import Path

import pytest


def _load_worker_management_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "skills"
        / "builtin"
        / "worker_management"
        / "scripts"
        / "execute.py"
    )
    spec = importlib.util.spec_from_file_location(
        "worker_management_execute_test",
        path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_worker_management_dispatch_inherits_runtime_delivery_defaults(
    monkeypatch,
):
    module = _load_worker_management_module()
    captured = {}

    async def fake_dispatch_worker(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "task_id": "tsk-1", "summary": "queued"}

    monkeypatch.setattr(module.dispatch_tools, "dispatch_worker", fake_dispatch_worker)
    monkeypatch.setenv("X_BOT_RUNTIME_PLATFORM", "telegram")
    monkeypatch.setenv("X_BOT_RUNTIME_CHAT_ID", "chat-42")
    monkeypatch.setenv("X_BOT_RUNTIME_SOURCE_USER_ID", "user-42")
    monkeypatch.setenv("X_BOT_RUNTIME_USER_ID", "worker::worker-main::user-42")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "execute.py",
            "dispatch",
            "查询今天无锡天气",
        ],
    )

    exit_code = await module._run()

    assert exit_code == 0
    assert captured["instruction"] == "查询今天无锡天气"
    assert captured["metadata"]["platform"] == "telegram"
    assert captured["metadata"]["chat_id"] == "chat-42"
    assert captured["metadata"]["user_id"] == "user-42"


@pytest.mark.asyncio
async def test_worker_management_dispatch_keeps_explicit_delivery_metadata(
    monkeypatch,
):
    module = _load_worker_management_module()
    captured = {}

    async def fake_dispatch_worker(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "task_id": "tsk-2", "summary": "queued"}

    monkeypatch.setattr(module.dispatch_tools, "dispatch_worker", fake_dispatch_worker)
    monkeypatch.setenv("X_BOT_RUNTIME_PLATFORM", "telegram")
    monkeypatch.setenv("X_BOT_RUNTIME_CHAT_ID", "chat-env")
    monkeypatch.setenv("X_BOT_RUNTIME_SOURCE_USER_ID", "user-env")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "execute.py",
            "dispatch",
            "做一个部署检查",
            "--metadata",
            '{"platform":"discord","chat_id":"chat-explicit","user_id":"user-explicit"}',
        ],
    )

    exit_code = await module._run()

    assert exit_code == 0
    assert captured["metadata"]["platform"] == "discord"
    assert captured["metadata"]["chat_id"] == "chat-explicit"
    assert captured["metadata"]["user_id"] == "user-explicit"
