from __future__ import annotations

import os

import pytest

from core.soul_store import SoulPayload
from core.waiting_phrase_store import WaitingPhraseStore


def test_should_refresh_for_soul_uses_ten_minute_threshold(tmp_path):
    store = WaitingPhraseStore(refresh_threshold_seconds=600)
    soul_path = (tmp_path / "SOUL.MD").resolve()
    phrase_path = (tmp_path / "WAITING_PHRASES.MD").resolve()

    soul_path.write_text("# Soul\n", encoding="utf-8")

    assert store._should_refresh_for_soul(soul_path, phrase_path) is True

    phrase_path.write_text("# Waiting Phrases\n", encoding="utf-8")
    os.utime(soul_path, (2000, 2000))
    os.utime(phrase_path, (1500, 1500))
    assert store._should_refresh_for_soul(soul_path, phrase_path) is False

    os.utime(soul_path, (2201, 2201))
    os.utime(phrase_path, (1500, 1500))
    assert store._should_refresh_for_soul(soul_path, phrase_path) is True


@pytest.mark.asyncio
async def test_refresh_if_needed_writes_and_loads_generated_markdown(
    tmp_path, monkeypatch
):
    store = WaitingPhraseStore(refresh_threshold_seconds=600)
    soul_path = (tmp_path / "SOUL.MD").resolve()
    soul_path.write_text("# Worker SOUL\n- Name: 阿黑\n", encoding="utf-8")

    payload = SoulPayload(
        agent_kind="worker",
        agent_id="worker-main",
        path=str(soul_path),
        content=soul_path.read_text(encoding="utf-8"),
        updated_at="2026-02-26T00:00:00+08:00",
        latest_version_id="",
    )

    async def _fake_generate(_payload: SoulPayload):
        return ["📨 已收到，准备执行"], ["🤖 正在生成结果"]

    monkeypatch.setattr(store, "_generate_phrase_pools_with_llm", _fake_generate)

    refreshed = await store.refresh_if_needed_for_payload(payload)
    assert refreshed is True

    phrase_path = store.phrase_path_for_soul_payload(payload)
    loaded = store.load_phrase_pools(phrase_path)
    assert loaded is not None
    received, loading = loaded
    assert "📨 已收到，准备执行" in received
    assert "🤖 正在生成结果" in loading
