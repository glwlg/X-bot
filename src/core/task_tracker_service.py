from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict

from core.background_delivery import push_background_text
from core.heartbeat_store import heartbeat_store
from core.task_inbox import TaskEnvelope, task_inbox


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


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


def _short(value: Any, *, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


class TaskTrackerService:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def _response(
        *,
        ok: bool,
        summary: str,
        text: str = "",
        data: Dict[str, Any] | None = None,
        error_code: str = "",
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "ok": bool(ok),
            "summary": _short(summary),
            "text": _short(text or summary, limit=4000),
            "data": dict(data or {}),
            "terminal": False,
            "task_outcome": "",
        }
        if not ok:
            payload["error_code"] = _short(
                error_code or "task_tracker_failed", limit=80
            )
            payload["message"] = payload["text"]
            payload["failure_mode"] = "recoverable"
        return payload

    @staticmethod
    def _serialize_event(event: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "at": str(event.get("at") or "").strip(),
            "event": str(event.get("event") or "").strip(),
            "detail": str(event.get("detail") or "").strip(),
            "extra": dict(event.get("extra") or {})
            if isinstance(event.get("extra"), dict)
            else {},
        }

    def _serialize_task(
        self,
        task: TaskEnvelope,
        *,
        include_events: bool = False,
        event_limit: int = 20,
    ) -> Dict[str, Any]:
        events = [
            self._serialize_event(item)
            for item in list(task.events or [])
            if isinstance(item, dict)
        ]
        payload = {
            "task_id": str(task.task_id or "").strip(),
            "source": str(task.source or "").strip(),
            "goal": str(task.goal or "").strip(),
            "user_id": str(task.user_id or "").strip(),
            "status": str(task.status or "").strip(),
            "updated_at": str(task.updated_at or "").strip(),
            "created_at": str(task.created_at or "").strip(),
            "priority": str(task.priority or "").strip(),
            "metadata": dict(task.metadata or {}),
            "result": dict(task.result or {}),
            "output": dict(task.output or {}),
            "final_output": str(task.final_output or "").strip(),
        }
        if events:
            payload["last_event"] = events[-1]
        if include_events:
            safe_limit = max(1, int(event_limit or 20))
            payload["events"] = events[-safe_limit:]
        return payload

    @staticmethod
    def _followup(task: TaskEnvelope) -> Dict[str, Any]:
        metadata = dict(task.metadata or {})
        followup = metadata.get("followup")
        return dict(followup) if isinstance(followup, dict) else {}

    @staticmethod
    def _is_due(task: TaskEnvelope, *, now: datetime) -> bool:
        followup = TaskTrackerService._followup(task)
        review_after = _parse_iso(followup.get("next_review_after"))
        if review_after is None:
            return True
        return review_after <= now

    @staticmethod
    async def _get_owned_task(user_id: str, task_id: str) -> TaskEnvelope | None:
        task = await task_inbox.get(task_id)
        if task is None:
            return None
        if user_id and str(task.user_id or "").strip() != str(user_id or "").strip():
            return None
        return task

    async def list_open(
        self,
        *,
        user_id: str,
        limit: int = 20,
        due_only: bool = True,
        event_limit: int = 1,
    ) -> Dict[str, Any]:
        safe_user_id = str(user_id or "").strip()
        if not safe_user_id:
            return self._response(
                ok=False,
                summary="task_tracker list_open failed",
                text="user_id is required",
                error_code="invalid_args",
            )
        rows = await task_inbox.list_open(
            user_id=safe_user_id,
            limit=0,
        )
        now = datetime.now().astimezone()
        filtered: list[TaskEnvelope] = []
        for task in rows:
            if str(task.source or "").strip().lower() == "heartbeat":
                continue
            if due_only and not self._is_due(task, now=now):
                continue
            filtered.append(task)
        filtered.sort(
            key=lambda item: (
                _parse_iso(self._followup(item).get("next_review_after"))
                or datetime.min.replace(tzinfo=now.tzinfo),
                str(item.updated_at or ""),
            )
        )
        tasks = [
            self._serialize_task(task, include_events=False, event_limit=event_limit)
            for task in filtered[: max(1, int(limit or 1))]
        ]
        return self._response(
            ok=True,
            summary=f"{len(tasks)} open task(s)",
            text=(
                "No open tasks due for review."
                if not tasks
                else "\n".join(
                    [
                        f"- [{item['status']}] {item['task_id']}: {item['goal']}"
                        for item in tasks
                    ]
                )
            ),
            data={"tasks": tasks},
        )

    async def get(
        self,
        *,
        user_id: str,
        task_id: str,
        event_limit: int = 20,
    ) -> Dict[str, Any]:
        safe_user_id = str(user_id or "").strip()
        safe_task_id = str(task_id or "").strip()
        if not safe_task_id:
            return self._response(
                ok=False,
                summary="task_tracker get failed",
                text="task_id is required",
                error_code="invalid_args",
            )
        task = await self._get_owned_task(safe_user_id, safe_task_id)
        if task is None:
            return self._response(
                ok=False,
                summary="task_tracker get failed",
                text="task not found",
                error_code="task_not_found",
            )
        payload = self._serialize_task(
            task, include_events=True, event_limit=event_limit
        )
        return self._response(
            ok=True,
            summary=f"task {safe_task_id}",
            text=f"[{payload['status']}] {payload['goal']}",
            data={"task": payload},
        )

    async def _sync_session_snapshot(
        self,
        *,
        task_id: str,
        task: TaskEnvelope,
        user_id: str,
        status: str,
        result_summary: str,
    ) -> None:
        active = await heartbeat_store.get_session_active_task(user_id)
        summary_to_store = str(result_summary or "").strip()
        if not summary_to_store:
            summary_to_store = str((active or {}).get("result_summary") or "").strip()
        if not summary_to_store:
            summary_to_store = str(
                (active or {}).get("last_user_visible_summary") or ""
            ).strip()
        if not summary_to_store:
            summary_to_store = str((task.output or {}).get("text") or "").strip()
        if not summary_to_store:
            summary_to_store = str((task.result or {}).get("summary") or "").strip()
        active_task_id = str((active or {}).get("task_inbox_id") or "").strip()
        session_task_id = str((active or {}).get("session_task_id") or "").strip()
        session_status = "done" if status == "completed" else status
        should_clear = session_status in {"done", "failed", "cancelled", "timed_out"}
        if active and task_id in {active_task_id, session_task_id}:
            await heartbeat_store.update_session_active_task(
                user_id,
                status=session_status,
                result_summary=summary_to_store,
                needs_confirmation=False,
                confirmation_deadline="",
                clear_active=should_clear,
            )
            return

        if session_status != "waiting_external":
            return

        metadata = dict(task.metadata or {})
        await heartbeat_store.set_session_active_task(
            user_id,
            {
                "id": task_id,
                "session_task_id": str(
                    metadata.get("session_task_id") or task_id
                ).strip()
                or task_id,
                "task_inbox_id": task_id,
                "goal": str(task.goal or "").strip(),
                "status": "waiting_external",
                "source": str(task.source or "").strip(),
                "result_summary": summary_to_store,
                "needs_confirmation": False,
                "confirmation_deadline": "",
                "stage_index": int(metadata.get("stage_index") or 0),
                "stage_total": int(metadata.get("stage_total") or 0),
                "stage_id": str(metadata.get("stage_id") or "").strip(),
                "stage_title": str(metadata.get("stage_title") or "").strip(),
                "attempt_index": int(metadata.get("attempt_index") or 0),
                "delivery_state": str(metadata.get("delivery_state") or "").strip(),
                "last_user_visible_summary": summary_to_store,
                "resume_window_until": str(
                    metadata.get("resume_window_until") or ""
                ).strip(),
            },
        )

    async def update(
        self,
        *,
        user_id: str,
        task_id: str,
        status: str = "",
        result_summary: str = "",
        done_when: str = "",
        next_review_after: str = "",
        refs: Dict[str, Any] | None = None,
        notes: str = "",
        announce_before_action: bool | None = None,
        last_observation: str = "",
        last_action_summary: str = "",
        announce_text: str = "",
        announce_key: str = "",
        announce_platform: str = "",
        announce_chat_id: str = "",
    ) -> Dict[str, Any]:
        safe_user_id = str(user_id or "").strip()
        safe_task_id = str(task_id or "").strip()
        if not safe_task_id or not safe_user_id:
            return self._response(
                ok=False,
                summary="task_tracker update failed",
                text="user_id and task_id are required",
                error_code="invalid_args",
            )

        lock = self._locks.setdefault(safe_task_id, asyncio.Lock())
        async with lock:
            task = await self._get_owned_task(safe_user_id, safe_task_id)
            if task is None:
                return self._response(
                    ok=False,
                    summary="task_tracker update failed",
                    text="task not found",
                    error_code="task_not_found",
                )

            now = _now_iso()
            followup = self._followup(task)
            if done_when:
                followup["done_when"] = str(done_when).strip()
            if next_review_after:
                followup["next_review_after"] = str(next_review_after).strip()
            if isinstance(refs, dict) and refs:
                current_refs = followup.get("refs")
                current_refs_obj = (
                    dict(current_refs) if isinstance(current_refs, dict) else {}
                )
                current_refs_obj.update(dict(refs))
                followup["refs"] = current_refs_obj
            if notes:
                followup["notes"] = str(notes).strip()
            if announce_before_action is not None:
                followup["announce_before_action"] = bool(announce_before_action)
            if last_observation:
                followup["last_review_at"] = now
                followup["last_observation"] = str(last_observation).strip()
            if last_action_summary:
                followup["last_review_at"] = now
                followup["last_action_summary"] = str(last_action_summary).strip()

            safe_status = (
                str(status or task.status or "running").strip().lower() or "running"
            )
            safe_summary = str(result_summary or "").strip()
            announcement_sent = False
            duplicate_announcement = False
            safe_announce_key = str(announce_key or "").strip()
            safe_announce_text = str(announce_text or "").strip()
            if safe_announce_text and safe_announce_key:
                duplicate_announcement = (
                    followup.get("last_announcement_key") == safe_announce_key
                )

            if safe_announce_text and not duplicate_announcement:
                followup["announcement_attempt_at"] = now
                followup["announcement_attempt_key"] = safe_announce_key
                await task_inbox.update_status(
                    safe_task_id,
                    safe_status,
                    event="task_tracker_updated",
                    detail=(safe_summary or safe_announce_text)[:180],
                    metadata={"followup": followup},
                    result={"summary": safe_summary} if safe_summary else {},
                    output={"text": safe_summary} if safe_summary else {},
                )

                platform = str(announce_platform or "").strip()
                chat_id = str(announce_chat_id or "").strip()
                session_id = ""
                if not platform or not chat_id:
                    target = await heartbeat_store.get_delivery_target(safe_user_id)
                    platform = platform or str(target.get("platform") or "").strip()
                    chat_id = chat_id or str(target.get("chat_id") or "").strip()
                    session_id = str(target.get("session_id") or "").strip()
                if platform and chat_id:
                    announcement_sent = bool(
                        await push_background_text(
                            platform=platform,
                            chat_id=chat_id,
                            text=safe_announce_text,
                            record_history=True,
                            history_user_id=safe_user_id,
                            history_session_id=session_id,
                        )
                    )
                if announcement_sent:
                    followup["last_announcement_at"] = now
                    followup["last_announcement_key"] = safe_announce_key

            update_fields: Dict[str, Any] = {
                "metadata": {"followup": followup},
            }
            if safe_summary:
                update_fields["result"] = {"summary": safe_summary}
                update_fields["output"] = {"text": safe_summary}
                if safe_status == "completed":
                    update_fields["final_output"] = safe_summary
            await task_inbox.update_status(
                safe_task_id,
                safe_status,
                event="task_tracker_updated",
                detail=(safe_summary or safe_status)[:180],
                **update_fields,
            )
            await self._sync_session_snapshot(
                task_id=safe_task_id,
                task=task,
                user_id=safe_user_id,
                status=safe_status,
                result_summary=safe_summary,
            )
            updated = await self._get_owned_task(safe_user_id, safe_task_id)
            payload = self._serialize_task(updated or task)
            return self._response(
                ok=True,
                summary=f"task {safe_task_id} updated",
                text=safe_summary or f"task {safe_task_id} updated",
                data={
                    "task": payload,
                    "announcement_sent": announcement_sent,
                    "announcement_skipped_duplicate": duplicate_announcement,
                },
            )

    async def handle(
        self,
        *,
        action: str,
        user_id: str,
        task_id: str = "",
        limit: int = 20,
        due_only: bool = True,
        event_limit: int = 20,
        status: str = "",
        result_summary: str = "",
        done_when: str = "",
        next_review_after: str = "",
        refs: Dict[str, Any] | None = None,
        notes: str = "",
        announce_before_action: bool | None = None,
        last_observation: str = "",
        last_action_summary: str = "",
        announce_text: str = "",
        announce_key: str = "",
        announce_platform: str = "",
        announce_chat_id: str = "",
    ) -> Dict[str, Any]:
        safe_action = str(action or "list_open").strip().lower() or "list_open"
        if safe_action == "list_open":
            return await self.list_open(
                user_id=user_id,
                limit=limit,
                due_only=bool(due_only),
                event_limit=event_limit,
            )
        if safe_action == "get":
            return await self.get(
                user_id=user_id,
                task_id=task_id,
                event_limit=event_limit,
            )
        if safe_action == "update":
            return await self.update(
                user_id=user_id,
                task_id=task_id,
                status=status,
                result_summary=result_summary,
                done_when=done_when,
                next_review_after=next_review_after,
                refs=refs,
                notes=notes,
                announce_before_action=announce_before_action,
                last_observation=last_observation,
                last_action_summary=last_action_summary,
                announce_text=announce_text,
                announce_key=announce_key,
                announce_platform=announce_platform,
                announce_chat_id=announce_chat_id,
            )
        return self._response(
            ok=False,
            summary="task_tracker failed",
            text=f"Unsupported task_tracker action: {safe_action}",
            error_code="unsupported_action",
        )


task_tracker_service = TaskTrackerService()
