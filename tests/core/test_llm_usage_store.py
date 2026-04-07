from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.llm_usage_store as llm_usage_module


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
    requests: int,
    success_requests: int,
    failed_requests: int,
    usage_requests: int,
    missing_usage_requests: int,
    estimated_token_requests: int,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    image_outputs: int = 0,
    cache_hit_requests: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    last_used_at: str = "",
) -> None:
    llm_usage_module.llm_usage_store._ensure_db()
    ts = last_used_at or f"{day}T12:00:00+08:00"
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
                image_outputs,
                cache_hit_requests,
                cache_read_tokens,
                cache_write_tokens,
                first_used_at,
                last_used_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
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
                image_outputs,
                cache_hit_requests,
                cache_read_tokens,
                cache_write_tokens,
                ts,
                ts,
            ),
        )


@pytest.mark.asyncio
async def test_wrap_openai_client_records_async_chat_tokens_and_cache_hits(
    tmp_path, monkeypatch
):
    _reset_llm_usage_store(tmp_path, monkeypatch)
    llm_usage_module.set_current_llm_usage_session_id("session-chat")

    class _FakeAsyncCompletions:
        async def create(self, **kwargs):
            assert kwargs["model"] == "text"
            return SimpleNamespace(
                usage=SimpleNamespace(
                    prompt_tokens=120,
                    completion_tokens=30,
                    total_tokens=150,
                    prompt_tokens_details=SimpleNamespace(
                        cached_tokens=40,
                        cache_creation_tokens=10,
                    ),
                )
            )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeAsyncCompletions())
    )
    wrapped = llm_usage_module.wrap_openai_client(
        fake_client,
        default_model_key="demo/text",
    )

    await wrapped.chat.completions.create(model="text", messages=[])

    summary = llm_usage_module.llm_usage_store.summarize()
    assert summary["requests"] == 1
    assert summary["success_requests"] == 1
    assert summary["cache_hit_requests"] == 1
    assert summary["cache_read_tokens"] == 40
    assert summary["cache_write_tokens"] == 10
    assert summary["models"][0]["model_key"] == "demo/text"
    assert summary["models"][0]["total_tokens"] == 150


@pytest.mark.asyncio
async def test_same_day_same_session_same_model_aggregates_into_one_row(
    tmp_path, monkeypatch
):
    db_path = _reset_llm_usage_store(tmp_path, monkeypatch)
    llm_usage_module.set_current_llm_usage_session_id("session-agg")

    class _FakeAsyncCompletions:
        async def create(self, **kwargs):
            _ = kwargs
            return SimpleNamespace(
                usage=SimpleNamespace(
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                )
            )

    wrapped = llm_usage_module.wrap_openai_client(
        SimpleNamespace(chat=SimpleNamespace(completions=_FakeAsyncCompletions())),
        default_model_key="demo/text",
    )

    await wrapped.chat.completions.create(model="text", messages=[{"role": "user", "content": "a"}])
    await wrapped.chat.completions.create(model="text", messages=[{"role": "user", "content": "b"}])

    summary = llm_usage_module.llm_usage_store.summarize()
    assert summary["requests"] == 2
    assert summary["total_tokens"] == 30

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            f"SELECT COUNT(*) FROM {llm_usage_module._USAGE_TABLE}"
        ).fetchone()
    assert row is not None
    assert int(row[0]) == 1


@pytest.mark.asyncio
async def test_wrap_openai_client_estimates_tokens_when_usage_missing(
    tmp_path, monkeypatch
):
    _reset_llm_usage_store(tmp_path, monkeypatch)
    llm_usage_module.set_current_llm_usage_session_id("session-est")

    class _FakeAsyncCompletions:
        async def create(self, **kwargs):
            assert kwargs["model"] == "text"
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="你好，我在。")
                    )
                ]
            )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeAsyncCompletions())
    )
    wrapped = llm_usage_module.wrap_openai_client(
        fake_client,
        default_model_key="demo/text",
    )

    await wrapped.chat.completions.create(
        model="text",
        messages=[{"role": "user", "content": "你好，介绍下你自己"}],
    )

    summary = llm_usage_module.llm_usage_store.summarize()
    assert summary["requests"] == 1
    assert summary["usage_requests"] == 0
    assert summary["estimated_token_requests"] == 1
    assert summary["input_tokens"] > 0
    assert summary["output_tokens"] > 0
    assert summary["total_tokens"] == summary["input_tokens"] + summary["output_tokens"]


@pytest.mark.asyncio
async def test_wrap_openai_client_records_sdk_style_async_method_output_tokens(
    tmp_path, monkeypatch
):
    _reset_llm_usage_store(tmp_path, monkeypatch)
    llm_usage_module.set_current_llm_usage_session_id("session-sdk-style")

    class _SdkLikeAsyncCompletions:
        def create(self, **kwargs):
            assert kwargs["model"] == "text"

            async def _run():
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content="这是 SDK 风格的异步输出")
                        )
                    ]
                )

            return _run()

    wrapped = llm_usage_module.wrap_openai_client(
        SimpleNamespace(chat=SimpleNamespace(completions=_SdkLikeAsyncCompletions())),
        default_model_key="demo/text",
    )

    await wrapped.chat.completions.create(
        model="text",
        messages=[{"role": "user", "content": "请回复一句话"}],
    )

    summary = llm_usage_module.llm_usage_store.summarize()
    assert summary["requests"] == 1
    assert summary["estimated_token_requests"] == 1
    assert summary["input_tokens"] > 0
    assert summary["output_tokens"] > 0


@pytest.mark.asyncio
async def test_wrap_openai_client_estimates_output_tokens_from_response_fallback(
    tmp_path, monkeypatch
):
    _reset_llm_usage_store(tmp_path, monkeypatch)
    llm_usage_module.set_current_llm_usage_session_id("session-fallback")

    class _WeirdResponse:
        def __init__(self):
            self.result = {"answer": {"text": "这是一个兜底输出"}}

    class _FakeAsyncCompletions:
        async def create(self, **kwargs):
            assert kwargs["model"] == "text"
            return _WeirdResponse()

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeAsyncCompletions())
    )
    wrapped = llm_usage_module.wrap_openai_client(
        fake_client,
        default_model_key="demo/text",
    )

    await wrapped.chat.completions.create(
        model="text",
        messages=[{"role": "user", "content": "说一句话"}],
    )

    summary = llm_usage_module.llm_usage_store.summarize()
    assert summary["estimated_token_requests"] == 1
    assert summary["output_tokens"] > 0


def test_wrap_openai_client_records_sync_image_requests_without_usage(
    tmp_path, monkeypatch
):
    _reset_llm_usage_store(tmp_path, monkeypatch)
    llm_usage_module.set_current_llm_usage_session_id("session-image")

    class _FakeImages:
        def generate(self, **kwargs):
            assert kwargs["model"] == "image-gen"
            return SimpleNamespace(data=[SimpleNamespace(b64_json="payload")])

    fake_client = SimpleNamespace(images=_FakeImages())
    wrapped = llm_usage_module.wrap_openai_client(
        fake_client,
        default_model_key="demo/image-gen",
    )

    wrapped.images.generate(model="image-gen", prompt="draw")

    summary = llm_usage_module.llm_usage_store.summarize()
    assert summary["requests"] == 1
    assert summary["usage_requests"] == 0
    assert summary["missing_usage_requests"] == 1
    assert summary["estimated_token_requests"] == 1
    assert summary["input_tokens"] > 0
    assert summary["image_outputs"] == 1
    assert summary["models"][0]["model_key"] == "demo/image-gen"
    assert summary["models"][0]["image_outputs"] == 1


def test_summarize_models_defaults_to_today_and_includes_image_outputs(
    tmp_path, monkeypatch
):
    db_path = _reset_llm_usage_store(tmp_path, monkeypatch)
    today = datetime.now().astimezone().date().isoformat()

    _insert_usage_row(
        db_path,
        day=today,
        session_id="session-a",
        model_key="demo/text",
        requests=2,
        success_requests=2,
        failed_requests=0,
        usage_requests=2,
        missing_usage_requests=0,
        estimated_token_requests=0,
        input_tokens=100,
        output_tokens=20,
        total_tokens=120,
    )
    _insert_usage_row(
        db_path,
        day=today,
        session_id="session-b",
        model_key="demo/image",
        requests=1,
        success_requests=1,
        failed_requests=0,
        usage_requests=0,
        missing_usage_requests=1,
        estimated_token_requests=1,
        input_tokens=12,
        output_tokens=0,
        total_tokens=12,
        image_outputs=3,
    )

    summary = llm_usage_module.llm_usage_store.summarize_models(
        ["demo/text", "demo/image", "demo/missing"]
    )

    assert summary["demo/text"]["total_tokens"] == 120
    assert summary["demo/image"]["image_outputs"] == 3
    assert summary["demo/missing"]["total_tokens"] == 0
    assert summary["demo/missing"]["image_outputs"] == 0


@pytest.mark.asyncio
async def test_wrap_openai_client_records_failures(tmp_path, monkeypatch):
    _reset_llm_usage_store(tmp_path, monkeypatch)
    llm_usage_module.set_current_llm_usage_session_id("session-fail")

    class _FakeAsyncCompletions:
        async def create(self, **kwargs):
            _ = kwargs
            raise RuntimeError("upstream unavailable")

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeAsyncCompletions())
    )
    wrapped = llm_usage_module.wrap_openai_client(
        fake_client,
        default_model_key="demo/router",
    )

    with pytest.raises(RuntimeError):
        await wrapped.chat.completions.create(model="router", messages=[])

    summary = llm_usage_module.llm_usage_store.summarize()
    assert summary["requests"] == 1
    assert summary["failed_requests"] == 1
    assert summary["models"][0]["model_key"] == "demo/router"


def test_summarize_supports_day_filter(tmp_path, monkeypatch):
    db_path = _reset_llm_usage_store(tmp_path, monkeypatch)

    now = datetime.now().astimezone().replace(microsecond=0)
    yesterday = (now - timedelta(days=1)).date().isoformat()
    today = now.date().isoformat()

    _insert_usage_row(
        db_path,
        day=yesterday,
        session_id="session-yesterday",
        model_key="demo/yesterday",
        requests=1,
        success_requests=1,
        failed_requests=0,
        usage_requests=1,
        missing_usage_requests=0,
        estimated_token_requests=0,
        input_tokens=11,
        output_tokens=5,
        total_tokens=16,
    )
    _insert_usage_row(
        db_path,
        day=today,
        session_id="session-today",
        model_key="demo/today",
        requests=1,
        success_requests=1,
        failed_requests=0,
        usage_requests=1,
        missing_usage_requests=0,
        estimated_token_requests=0,
        input_tokens=7,
        output_tokens=3,
        total_tokens=10,
    )

    summary = llm_usage_module.llm_usage_store.summarize(day=today)
    assert summary["requests"] == 1
    assert summary["models"][0]["model_key"] == "demo/today"


def test_reset_removes_usage_rows(tmp_path, monkeypatch):
    db_path = _reset_llm_usage_store(tmp_path, monkeypatch)
    today = datetime.now().astimezone().date().isoformat()

    _insert_usage_row(
        db_path,
        day=today,
        session_id="session-a",
        model_key="demo/a",
        requests=1,
        success_requests=1,
        failed_requests=0,
        usage_requests=1,
        missing_usage_requests=0,
        estimated_token_requests=0,
        input_tokens=1,
        output_tokens=1,
        total_tokens=2,
    )
    _insert_usage_row(
        db_path,
        day=today,
        session_id="session-b",
        model_key="demo/b",
        requests=1,
        success_requests=1,
        failed_requests=0,
        usage_requests=1,
        missing_usage_requests=0,
        estimated_token_requests=0,
        input_tokens=1,
        output_tokens=1,
        total_tokens=2,
    )

    removed = llm_usage_module.llm_usage_store.reset()

    assert removed == 2
    assert llm_usage_module.llm_usage_store.summarize()["requests"] == 0
