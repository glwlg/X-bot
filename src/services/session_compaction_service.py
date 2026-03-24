from __future__ import annotations

import logging
from typing import Any

from core.config import get_client_for_model
from core.llm_usage_store import llm_usage_session
from core.model_config import get_current_model
from core.state_store import get_session_entries, replace_session_entries
from services.openai_adapter import generate_text

logger = logging.getLogger(__name__)

SESSION_SUMMARY_PREFIX = "【会话压缩摘要】"
SESSION_MEMORY_PREFIX = "【会话记忆种子】"
VISIBLE_DIALOG_ROLES = {"user", "model"}


def _dialog_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        item
        for item in list(rows or [])
        if str(item.get("role") or "").strip().lower() in VISIBLE_DIALOG_ROLES
        and str(item.get("content") or "").strip()
    ]


def _system_rows_with_prefix(
    rows: list[dict[str, str]],
    prefix: str,
) -> list[dict[str, str]]:
    return [
        item
        for item in list(rows or [])
        if str(item.get("role") or "").strip().lower() == "system"
        and str(item.get("content") or "").startswith(prefix)
    ]


def _render_dialog_lines(rows: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for item in rows:
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        label = "用户" if role == "user" else "助手"
        lines.append(f"{label}: {content}")
    return "\n".join(lines).strip()


def _fallback_summary(
    *,
    previous_summary: str,
    older_rows: list[dict[str, str]],
) -> str:
    snippets: list[str] = []
    if previous_summary:
        snippets.append(previous_summary.strip())
    for item in older_rows[-12:]:
        content = " ".join(str(item.get("content") or "").split())
        if not content:
            continue
        label = "用户" if str(item.get("role") or "") == "user" else "助手"
        snippets.append(f"- {label}: {content[:180]}")
    if not snippets:
        return ""
    return "本会话较早内容摘要：\n" + "\n".join(snippets[:16])


class SessionCompactionService:
    async def compact_session(
        self,
        *,
        user_id: str,
        session_id: str,
        keep_recent: int = 10,
        threshold: int = 100,
        force: bool = False,
    ) -> dict[str, Any]:
        rows = await get_session_entries(user_id, session_id)
        if not rows:
            return {
                "ok": True,
                "compacted": False,
                "reason": "empty_session",
                "dialog_count": 0,
                "compressed_count": 0,
                "kept_recent": 0,
            }

        dialog_rows = _dialog_rows(rows)
        dialog_count = len(dialog_rows)
        if not force and dialog_count <= max(1, int(threshold)):
            return {
                "ok": True,
                "compacted": False,
                "reason": "below_threshold",
                "dialog_count": dialog_count,
                "compressed_count": 0,
                "kept_recent": min(dialog_count, max(1, int(keep_recent))),
            }

        keep_recent = max(1, int(keep_recent))
        preserved_recent = dialog_rows[-keep_recent:]
        older_rows = dialog_rows[:-keep_recent]
        summary_rows = _system_rows_with_prefix(rows, SESSION_SUMMARY_PREFIX)
        previous_summary = ""
        if summary_rows:
            previous_summary = str(summary_rows[-1].get("content") or "").strip()

        if not older_rows and not previous_summary:
            return {
                "ok": True,
                "compacted": False,
                "reason": "nothing_to_compact",
                "dialog_count": dialog_count,
                "compressed_count": 0,
                "kept_recent": len(preserved_recent),
            }

        summary_text = await self._summarize_history(
            user_id=user_id,
            session_id=session_id,
            previous_summary=previous_summary,
            older_rows=older_rows,
        )
        if not summary_text:
            summary_text = _fallback_summary(
                previous_summary=previous_summary,
                older_rows=older_rows,
            )
        if summary_text and not summary_text.startswith(SESSION_SUMMARY_PREFIX):
            summary_text = f"{SESSION_SUMMARY_PREFIX}\n{summary_text.strip()}"

        memory_rows = _system_rows_with_prefix(rows, SESSION_MEMORY_PREFIX)
        rebuilt_rows: list[dict[str, str]] = []
        if memory_rows:
            rebuilt_rows.append(memory_rows[-1])
        if summary_text:
            rebuilt_rows.append({"role": "system", "content": summary_text.strip()})
        rebuilt_rows.extend(preserved_recent)

        ok = await replace_session_entries(user_id, session_id, rebuilt_rows)
        return {
            "ok": bool(ok),
            "compacted": bool(ok),
            "reason": "compacted" if ok else "write_failed",
            "dialog_count": dialog_count,
            "compressed_count": len(older_rows),
            "kept_recent": len(preserved_recent),
            "has_summary": bool(summary_text),
        }

    async def _summarize_history(
        self,
        *,
        user_id: str,
        session_id: str,
        previous_summary: str,
        older_rows: list[dict[str, str]],
    ) -> str:
        source_blocks: list[str] = []
        if previous_summary:
            source_blocks.append(previous_summary.strip())
        rendered_dialog = _render_dialog_lines(older_rows)
        if rendered_dialog:
            source_blocks.append(rendered_dialog)
        source_text = "\n\n".join(block for block in source_blocks if block).strip()
        if not source_text:
            return ""
        if len(source_text) > 18000:
            source_text = source_text[-18000:]

        prompt = (
            "请把下面这段更早的会话内容压缩成一段后续对话可复用的中文摘要。\n"
            "要求：\n"
            "1. 保留用户稳定偏好、身份信息、约束条件、重要事实。\n"
            "2. 保留未完成事项、待跟进项、最近决策与结论。\n"
            "3. 不要出现“以上/下面/本段”之类元话术，不要写分析过程。\n"
            "4. 输出简洁的要点列表，控制在 12 条以内。\n"
            "5. 如果存在旧摘要，要把旧摘要与新内容融合成一份更新后的滚动摘要。\n\n"
            f"会话内容：\n{source_text}"
        )

        try:
            model_name = get_current_model()
            client = get_client_for_model(model_name, is_async=True)
            if client is None:
                raise RuntimeError("OpenAI async client is not initialized")
            with llm_usage_session(session_id or user_id):
                summary = await generate_text(
                    async_client=client,
                    model=model_name,
                    contents=prompt,
                    config={
                        "system_instruction": (
                            "你是会话压缩助手。"
                            "只输出可直接作为后续对话上下文的摘要，不要附加说明。"
                        ),
                    },
                )
            return str(summary or "").strip()
        except Exception as exc:
            logger.warning("Session compaction summarization failed: %s", exc)
            return ""


session_compaction_service = SessionCompactionService()
