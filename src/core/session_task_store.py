from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List

from core.heartbeat_store import heartbeat_store
from core.task_cards import format_followup_context
from core.task_inbox import TaskEnvelope, task_inbox

_FOLLOWUP_CUES = {
    "继续",
    "继续吧",
    "继续执行",
    "继续处理",
    "继续下去",
    "那继续",
    "好",
    "好的",
    "需要",
    "需要的",
    "可以",
}


def _safe_text(value: Any, *, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


def _parse_iso(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed


def _now_local() -> datetime:
    return datetime.now().astimezone()


def _stage_id_from_metadata(metadata: Dict[str, Any]) -> str:
    if _safe_text(metadata.get("stage_id"), limit=80):
        return _safe_text(metadata.get("stage_id"), limit=80)
    stage_plan = metadata.get("stage_plan")
    if isinstance(stage_plan, dict):
        return _safe_text(stage_plan.get("current_stage_id"), limit=80)
    return ""


@dataclass
class SessionTaskSnapshot:
    session_task_id: str
    task_inbox_id: str
    user_id: str
    status: str
    current_stage_id: str = ""
    stage_index: int = 0
    stage_total: int = 0
    stage_title: str = ""
    attempt_index: int = 0
    delivery_state: str = ""
    last_user_visible_summary: str = ""
    resume_window_until: str = ""
    task_goal: str = ""
    original_user_request: str = ""
    updated_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class SessionTaskStore:
    @staticmethod
    def _snapshot_from_task(task: TaskEnvelope) -> SessionTaskSnapshot:
        metadata = dict(task.metadata or {})
        session_task_id = _safe_text(metadata.get("session_task_id"), limit=80) or _safe_text(
            task.task_id,
            limit=80,
        )
        output = dict(task.output or {})
        result = dict(task.result or {})
        payload = result.get("payload")
        payload_obj = dict(payload) if isinstance(payload, dict) else {}
        summary = (
            _safe_text(metadata.get("last_user_visible_summary"), limit=2400)
            or _safe_text(task.final_output, limit=2400)
            or _safe_text(output.get("text"), limit=2400)
            or _safe_text(payload_obj.get("text"), limit=2400)
            or _safe_text(result.get("summary"), limit=2400)
        )
        task_goal = (
            _safe_text(metadata.get("task_goal"), limit=4000)
            or _safe_text(metadata.get("original_user_request"), limit=4000)
            or _safe_text(task.goal, limit=4000)
        )
        original_request = (
            _safe_text(metadata.get("original_user_request"), limit=4000) or task_goal
        )
        return SessionTaskSnapshot(
            session_task_id=session_task_id,
            task_inbox_id=_safe_text(task.task_id, limit=80),
            user_id=_safe_text(task.user_id, limit=80),
            status=_safe_text(task.status, limit=40).lower() or "pending",
            current_stage_id=_stage_id_from_metadata(metadata),
            stage_index=max(0, int(metadata.get("stage_index") or 0)),
            stage_total=max(0, int(metadata.get("stage_total") or 0)),
            stage_title=_safe_text(metadata.get("stage_title"), limit=200),
            attempt_index=max(0, int(metadata.get("attempt_index") or 0)),
            delivery_state=_safe_text(metadata.get("delivery_state"), limit=40).lower(),
            last_user_visible_summary=summary,
            resume_window_until=_safe_text(
                metadata.get("resume_window_until"),
                limit=64,
            ),
            task_goal=task_goal,
            original_user_request=original_request,
            updated_at=_safe_text(task.updated_at, limit=64),
            metadata=metadata,
        )

    @staticmethod
    def _snapshot_from_active_task(
        user_id: str,
        active_task: Dict[str, Any],
        *,
        base: SessionTaskSnapshot | None = None,
    ) -> SessionTaskSnapshot:
        metadata = dict(base.metadata if base else {})
        session_task_id = (
            _safe_text(active_task.get("session_task_id"), limit=80)
            or _safe_text(active_task.get("task_inbox_id"), limit=80)
            or (base.session_task_id if base else "")
            or _safe_text(active_task.get("id"), limit=80)
        )
        task_inbox_id = (
            _safe_text(active_task.get("task_inbox_id"), limit=80)
            or (base.task_inbox_id if base else "")
            or session_task_id
        )
        return SessionTaskSnapshot(
            session_task_id=session_task_id,
            task_inbox_id=task_inbox_id,
            user_id=_safe_text(user_id, limit=80),
            status=_safe_text(active_task.get("status"), limit=40).lower()
            or (base.status if base else "running"),
            current_stage_id=_safe_text(active_task.get("stage_id"), limit=80)
            or (base.current_stage_id if base else ""),
            stage_index=max(
                0,
                int(active_task.get("stage_index") or (base.stage_index if base else 0)),
            ),
            stage_total=max(
                0,
                int(active_task.get("stage_total") or (base.stage_total if base else 0)),
            ),
            stage_title=_safe_text(active_task.get("stage_title"), limit=200)
            or (base.stage_title if base else ""),
            attempt_index=max(
                0,
                int(active_task.get("attempt_index") or (base.attempt_index if base else 0)),
            ),
            delivery_state=_safe_text(active_task.get("delivery_state"), limit=40).lower()
            or (base.delivery_state if base else ""),
            last_user_visible_summary=_safe_text(
                active_task.get("last_user_visible_summary"),
                limit=2400,
            )
            or _safe_text(active_task.get("result_summary"), limit=2400)
            or (base.last_user_visible_summary if base else ""),
            resume_window_until=_safe_text(active_task.get("resume_window_until"), limit=64)
            or (base.resume_window_until if base else ""),
            task_goal=_safe_text(active_task.get("goal"), limit=4000)
            or (base.task_goal if base else ""),
            original_user_request=(base.original_user_request if base else ""),
            updated_at=_safe_text(active_task.get("updated_at"), limit=64)
            or (base.updated_at if base else ""),
            metadata=metadata,
        )

    async def get(self, session_task_id: str) -> SessionTaskSnapshot | None:
        safe_id = _safe_text(session_task_id, limit=80)
        if not safe_id:
            return None
        direct = await task_inbox.get(safe_id)
        if direct is not None:
            return self._snapshot_from_task(direct)
        recent = await task_inbox.list_recent(limit=50)
        for task in recent:
            metadata = dict(task.metadata or {})
            if _safe_text(metadata.get("session_task_id"), limit=80) == safe_id:
                return self._snapshot_from_task(task)
        return None

    async def get_active(self, user_id: str) -> SessionTaskSnapshot | None:
        safe_user_id = _safe_text(user_id, limit=80)
        if not safe_user_id:
            return None
        active_task = await heartbeat_store.get_session_active_task(safe_user_id)
        if not isinstance(active_task, dict):
            return None
        task_inbox_id = _safe_text(
            active_task.get("task_inbox_id") or active_task.get("session_task_id"),
            limit=80,
        )
        base = await self.get(task_inbox_id) if task_inbox_id else None
        return self._snapshot_from_active_task(
            safe_user_id,
            active_task,
            base=base,
        )

    async def list_recent_completed(
        self,
        user_id: str,
        *,
        limit: int = 5,
    ) -> List[SessionTaskSnapshot]:
        safe_user_id = _safe_text(user_id, limit=80)
        if not safe_user_id:
            return []
        now = _now_local()
        recent = await task_inbox.list_recent(user_id=safe_user_id, limit=max(20, limit * 8))
        rows: List[SessionTaskSnapshot] = []
        for task in recent:
            if _safe_text(task.status, limit=40).lower() != "completed":
                continue
            snapshot = self._snapshot_from_task(task)
            if not snapshot.resume_window_until:
                continue
            resume_until = _parse_iso(snapshot.resume_window_until)
            if resume_until is None or resume_until <= now:
                continue
            if not snapshot.last_user_visible_summary:
                continue
            rows.append(snapshot)
            if len(rows) >= max(1, int(limit or 1)):
                break
        return rows

    async def match_followup(
        self,
        user_id: str,
        user_message: str,
    ) -> Dict[str, Any] | None:
        safe_user_id = _safe_text(user_id, limit=80)
        raw_message = _safe_text(user_message, limit=200)
        normalized = raw_message.lower().strip().strip("。.!！?？")
        if not safe_user_id or not normalized:
            return None
        if normalized not in _FOLLOWUP_CUES and not normalized.startswith("继续"):
            return None

        recent = await self.list_recent_completed(safe_user_id, limit=1)
        if not recent:
            return None
        snapshot = recent[0]
        return {
            "session_task_id": snapshot.session_task_id,
            "task_inbox_id": snapshot.task_inbox_id,
            "context_text": format_followup_context(snapshot),
            "snapshot": snapshot,
        }


session_task_store = SessionTaskStore()
