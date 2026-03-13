from __future__ import annotations

from typing import Any, Dict

from core.task_tracker_service import task_tracker_service


class TaskTrackerTools:
    async def task_tracker(
        self,
        *,
        action: str = "list_open",
        user_id: str = "",
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
        return await task_tracker_service.handle(
            action=action,
            user_id=user_id,
            task_id=task_id,
            limit=limit,
            due_only=due_only,
            event_limit=event_limit,
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


task_tracker_tools = TaskTrackerTools()
