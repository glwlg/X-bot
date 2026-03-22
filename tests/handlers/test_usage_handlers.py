from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.llm_usage_store as llm_usage_module
import handlers.usage_handlers as usage_handlers
from handlers import usage_command as exported_usage_command
from handlers.usage_handlers import usage_command


class _FakeUser:
    def __init__(self, user_id: str):
        self.id = user_id


class _FakeMessage:
    def __init__(self, text: str, user_id: str):
        self.id = "m-usage"
        self.text = text
        self.user = _FakeUser(user_id)


class _FakeContext:
    def __init__(self, text: str, user_id: str = "u-usage"):
        self.message = _FakeMessage(text, user_id)
        self.replies: list[str] = []

    async def reply(self, text: str, **kwargs):
        _ = kwargs
        self.replies.append(str(text))
        return SimpleNamespace(id="reply")


def _reset_llm_usage_store(tmp_path: Path, monkeypatch) -> Path:
    db_path = (tmp_path / "bot_data.db").resolve()
    if db_path.exists():
        db_path.unlink()
    monkeypatch.setattr(llm_usage_module.llm_usage_store, "db_path", db_path)
    monkeypatch.setattr(llm_usage_module.llm_usage_store, "_db_ready", False)
    llm_usage_module._USAGE_SESSION_VAR.set("")
    return db_path


def _insert_usage_row(
    db_path: Path,
    *,
    day: str,
    session_id: str,
    model_key: str,
    total_tokens: int,
    input_tokens: int,
    output_tokens: int,
) -> None:
    llm_usage_module.llm_usage_store._ensure_db()
    ts = f"{day}T12:00:00+08:00"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            f"""
            INSERT INTO {llm_usage_module._USAGE_TABLE} (
                day,
                session_id,
                model_key,
                requests,
                success_requests,
                failed_requests,
                usage_requests,
                missing_usage_requests,
                estimated_token_requests,
                input_tokens,
                output_tokens,
                total_tokens,
                cache_hit_requests,
                cache_read_tokens,
                cache_write_tokens,
                first_used_at,
                last_used_at
            ) VALUES (?, ?, ?, 1, 1, 0, 1, 0, 0, ?, ?, ?, 0, 0, 0, ?, ?)
            """,
            (
                day,
                session_id,
                model_key,
                input_tokens,
                output_tokens,
                total_tokens,
                ts,
                ts,
            ),
        )


@pytest.mark.asyncio
async def test_usage_command_renders_model_usage_summary(monkeypatch, tmp_path):
    _reset_llm_usage_store(tmp_path, monkeypatch)

    async def _allow(_ctx):
        return True

    monkeypatch.setattr(usage_handlers, "check_permission_unified", _allow)
    llm_usage_module.set_current_llm_usage_session_id("session-text")

    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=200,
            completion_tokens=50,
            total_tokens=250,
            prompt_tokens_details=SimpleNamespace(cached_tokens=80),
        )
    )
    llm_usage_module.llm_usage_store.record_event(
        operation="chat.completions.create",
        default_model_key="demo/text",
        request_kwargs={"model": "text"},
        response=response,
        success=True,
    )

    ctx = _FakeContext("/usage")
    await usage_command(ctx)

    assert ctx.replies
    reply = ctx.replies[-1]
    assert "LLM Token 用量" in reply
    assert "demo/text" in reply
    assert "cache_hit=1" in reply


@pytest.mark.asyncio
async def test_usage_command_today_filters_to_current_day(monkeypatch, tmp_path):
    db_path = _reset_llm_usage_store(tmp_path, monkeypatch)

    async def _allow(_ctx):
        return True

    monkeypatch.setattr(usage_handlers, "check_permission_unified", _allow)

    now = datetime.now().astimezone()
    yesterday = (now - timedelta(days=1)).date().isoformat()
    today = now.date().isoformat()
    _insert_usage_row(
        db_path,
        day=yesterday,
        session_id="session-yesterday",
        model_key="demo/yesterday",
        total_tokens=15,
        input_tokens=10,
        output_tokens=5,
    )
    _insert_usage_row(
        db_path,
        day=today,
        session_id="session-today",
        model_key="demo/today",
        total_tokens=12,
        input_tokens=8,
        output_tokens=4,
    )

    ctx = _FakeContext("/usage today")
    await usage_command(ctx)

    reply = ctx.replies[-1]
    assert "今日 LLM Token 用量" in reply
    assert "demo/today" in reply
    assert "demo/yesterday" not in reply


@pytest.mark.asyncio
async def test_usage_command_reset_clears_store(monkeypatch, tmp_path):
    db_path = _reset_llm_usage_store(tmp_path, monkeypatch)

    async def _allow(_ctx):
        return True

    monkeypatch.setattr(usage_handlers, "check_permission_unified", _allow)
    today = datetime.now().astimezone().date().isoformat()
    _insert_usage_row(
        db_path,
        day=today,
        session_id="session-reset",
        model_key="demo/reset",
        total_tokens=2,
        input_tokens=1,
        output_tokens=1,
    )

    ctx = _FakeContext("/usage reset")
    await usage_command(ctx)

    assert "已重置 LLM 用量统计" in ctx.replies[-1]
    assert llm_usage_module.llm_usage_store.summarize()["requests"] == 0


def test_usage_command_is_exported_from_handlers_package():
    assert exported_usage_command is usage_command


def test_main_registers_usage_command():
    main_py = Path(__file__).resolve().parents[2] / "src" / "main.py"
    text = main_py.read_text(encoding="utf-8")

    assert 'on_command("usage", usage_command' in text
