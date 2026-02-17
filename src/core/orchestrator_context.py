from __future__ import annotations

import datetime
import contextlib
from dataclasses import dataclass
from typing import Any, Dict

from core.heartbeat_store import heartbeat_store
from core.task_inbox import task_inbox
from core.task_manager import task_manager
from core.tool_access_store import tool_access_store


@dataclass
class OrchestratorRuntimeContext:
    user_id: str
    user_data: Dict[str, Any]
    runtime_user_id: str
    platform_name: str
    worker_runtime_user: bool
    heartbeat_runtime_user: bool
    session_state_enabled: bool
    runtime_policy_ctx: Dict[str, Any]
    runtime_agent_kind: str
    manager_runtime: bool
    task_id: str
    task_inbox_id: str
    session_state_active: bool = False
    workspace_event_logged: bool = False

    @classmethod
    def from_message(cls, ctx: Any) -> "OrchestratorRuntimeContext":
        msg = getattr(ctx, "message", None)
        msg_user = getattr(msg, "user", None)
        user_id = str(getattr(msg_user, "id", "") or "")
        user_data = getattr(ctx, "user_data", None)
        if not isinstance(user_data, dict):
            user_data = {}
            with contextlib.suppress(Exception):
                setattr(ctx, "user_data", user_data)

        runtime_user_id = str(user_data.get("runtime_user_id") or "").strip() or user_id
        platform_name = str(getattr(msg, "platform", "") or "").strip().lower()
        worker_runtime_user = (
            platform_name == "worker_runtime" or runtime_user_id.startswith("worker::")
        )
        heartbeat_runtime_user = platform_name == "heartbeat_daemon"
        session_state_enabled = not worker_runtime_user and not heartbeat_runtime_user

        runtime_policy_ctx = tool_access_store.resolve_runtime_policy(
            runtime_user_id=runtime_user_id,
            platform=platform_name,
        )
        runtime_agent_kind = (
            str(runtime_policy_ctx.get("agent_kind") or "").strip().lower()
        )
        manager_runtime = runtime_agent_kind != "worker"

        task_info = task_manager.get_task_info(user_id)
        task_id = (
            task_info.get("task_id")
            if isinstance(task_info, dict) and task_info.get("task_id")
            else f"{int(datetime.datetime.now().timestamp())}"
        )
        task_inbox_id = str(user_data.get("task_inbox_id") or "").strip()

        return cls(
            user_id=user_id,
            user_data=user_data,
            runtime_user_id=runtime_user_id,
            platform_name=platform_name,
            worker_runtime_user=worker_runtime_user,
            heartbeat_runtime_user=heartbeat_runtime_user,
            session_state_enabled=session_state_enabled,
            runtime_policy_ctx=runtime_policy_ctx,
            runtime_agent_kind=runtime_agent_kind,
            manager_runtime=manager_runtime,
            task_id=str(task_id),
            task_inbox_id=task_inbox_id,
        )

    async def append_session_event(self, note: str) -> None:
        if not self.session_state_enabled:
            return
        await heartbeat_store.append_session_event(self.user_id, note)

    async def update_session_task(
        self,
        *,
        status: str | None = None,
        result_summary: str | None = None,
        clear_active: bool = False,
        needs_confirmation: bool | None = None,
        confirmation_deadline: str | None = None,
    ) -> None:
        if not self.session_state_enabled:
            return
        fields: Dict[str, Any] = {}
        if status is not None:
            fields["status"] = status
        if result_summary is not None:
            fields["result_summary"] = result_summary[:500]
        if needs_confirmation is not None:
            fields["needs_confirmation"] = bool(needs_confirmation)
        if confirmation_deadline is not None:
            fields["confirmation_deadline"] = confirmation_deadline
        if clear_active:
            fields["clear_active"] = True
        if fields:
            await heartbeat_store.update_session_active_task(self.user_id, **fields)

    async def update_task_inbox_status(
        self,
        *,
        status: str,
        event: str,
        detail: str = "",
        **fields: Any,
    ) -> None:
        if not self.task_inbox_id:
            return
        await task_inbox.update_status(
            self.task_inbox_id,
            status,
            event=event,
            detail=detail,
            **fields,
        )

    async def mark_manager_loop_started(self, task_goal: str) -> None:
        if not self.task_inbox_id:
            return
        await self.update_task_inbox_status(
            status="running",
            event="manager_loop_started",
            detail=(task_goal or "")[:180],
            manager_id="core-manager",
        )

    async def activate_session(
        self, *, task_goal: str, task_workspace_root: str
    ) -> None:
        if not self.session_state_enabled:
            return
        await heartbeat_store.set_session_active_task(
            self.user_id,
            {
                "id": self.task_id,
                "goal": task_goal,
                "status": "running",
                "source": "user_chat",
                "result_summary": "",
                "needs_confirmation": False,
                "confirmation_deadline": "",
            },
        )
        task_manager.set_heartbeat_path(
            self.user_id, str(heartbeat_store.heartbeat_path(self.user_id))
        )
        task_manager.set_active_task_id(self.user_id, self.task_id)
        task_manager.heartbeat(self.user_id, f"session:{self.task_id}:running")
        await self.append_session_event(f"session_started:{self.task_id}")
        self.session_state_active = True
        if task_workspace_root and not self.workspace_event_logged:
            await self.append_session_event(f"workspace_root:{task_workspace_root}")
            self.workspace_event_logged = True
