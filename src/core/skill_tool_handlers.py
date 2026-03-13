from __future__ import annotations

import shlex
from typing import Any, Awaitable, Callable, Dict

from core.task_inbox import task_inbox
from core.tools.codex_tools import codex_tools
from core.tools.dispatch_tools import dispatch_tools
from core.tools.git_tools import git_tools
from core.tools.gh_tools import gh_tools
from core.tools.repo_workspace_tools import repo_workspace_tools


SkillToolHandler = Callable[[Any, Dict[str, Any]], Awaitable[Dict[str, Any]]]


class SkillToolHandlerRegistry:
    def __init__(self) -> None:
        self._handlers: Dict[str, SkillToolHandler] = {}

    def register(self, handler_id: str, handler: SkillToolHandler) -> None:
        safe_handler_id = str(handler_id or "").strip()
        if not safe_handler_id:
            return
        self._handlers[safe_handler_id] = handler

    async def dispatch(
        self,
        handler_id: str,
        *,
        dispatcher: Any,
        args: Dict[str, Any],
    ) -> Dict[str, Any]:
        safe_handler_id = str(handler_id or "").strip()
        handler = self._handlers.get(safe_handler_id)
        if handler is None:
            return {
                "ok": False,
                "error_code": "unsupported_skill_tool_handler",
                "message": f"Unsupported skill tool handler: {safe_handler_id}",
                "failure_mode": "recoverable",
            }
        return await handler(dispatcher, dict(args or {}))


def _dispatch_metadata_from_runtime(
    dispatcher: Any,
    tool_args: Dict[str, Any],
) -> Dict[str, Any]:
    metadata = tool_args.get("metadata")
    metadata_obj = dict(metadata) if isinstance(metadata, dict) else {}
    ctx_user_data = getattr(dispatcher.ctx, "user_data", None)
    user_data = ctx_user_data if isinstance(ctx_user_data, dict) else {}
    msg = getattr(dispatcher.ctx, "message", None)
    msg_user = getattr(msg, "user", None)
    msg_chat = getattr(msg, "chat", None)
    if "user_id" not in metadata_obj:
        metadata_obj["user_id"] = str(getattr(msg_user, "id", "") or "")
    if "chat_id" not in metadata_obj:
        metadata_obj["chat_id"] = str(getattr(msg_chat, "id", "") or "")
    if "platform" not in metadata_obj:
        metadata_obj["platform"] = str(getattr(msg, "platform", "") or "")

    forced_platform = str(user_data.get("worker_delivery_platform") or "").strip()
    forced_chat_id = str(user_data.get("worker_delivery_chat_id") or "").strip()
    if forced_platform:
        metadata_obj["platform"] = forced_platform
    if forced_chat_id:
        metadata_obj["chat_id"] = forced_chat_id
    if "session_id" not in metadata_obj:
        metadata_obj["session_id"] = str(dispatcher.task_id or "")
    if "task_inbox_id" not in metadata_obj:
        metadata_obj["task_inbox_id"] = str(
            getattr(dispatcher, "task_inbox_id", "") or ""
        )
    if "session_task_id" not in metadata_obj:
        metadata_obj["session_task_id"] = str(
            metadata_obj.get("task_inbox_id") or ""
        ).strip() or str(dispatcher.task_id or "")
    if "original_user_request" not in metadata_obj:
        extractor = getattr(dispatcher, "_extract_user_request", None)
        if callable(extractor):
            metadata_obj["original_user_request"] = str(extractor() or "")
    if "task_goal" not in metadata_obj:
        metadata_obj["task_goal"] = str(
            tool_args.get("instruction")
            or metadata_obj.get("original_user_request")
            or ""
        )
    return metadata_obj


def _notify_target_from_dispatcher(dispatcher: Any) -> Dict[str, str]:
    ctx_user_data = getattr(dispatcher.ctx, "user_data", None)
    user_data = ctx_user_data if isinstance(ctx_user_data, dict) else {}
    msg = getattr(dispatcher.ctx, "message", None)
    msg_user = getattr(msg, "user", None)
    msg_chat = getattr(msg, "chat", None)

    platform = str(getattr(msg, "platform", "") or "").strip()
    chat_id = str(getattr(msg_chat, "id", "") or "").strip()
    user_id = str(getattr(msg_user, "id", "") or "").strip()

    forced_platform = str(user_data.get("worker_delivery_platform") or "").strip()
    forced_chat_id = str(user_data.get("worker_delivery_chat_id") or "").strip()
    if forced_platform:
        platform = forced_platform
    if forced_chat_id:
        chat_id = forced_chat_id

    return {
        "notify_platform": platform,
        "notify_chat_id": chat_id,
        "notify_user_id": user_id,
    }


def _normalize_cli_argv(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        try:
            return [
                str(item).strip() for item in shlex.split(value) if str(item).strip()
            ]
        except Exception:
            return [item.strip() for item in value.split() if item.strip()]
    return []


async def _list_workers_handler(
    dispatcher: Any,
    tool_args: Dict[str, Any],
) -> Dict[str, Any]:
    _ = (dispatcher, tool_args)
    return await dispatch_tools.list_workers()


async def _dispatch_worker_handler(
    dispatcher: Any,
    tool_args: Dict[str, Any],
) -> Dict[str, Any]:
    result = await dispatch_tools.dispatch_worker(
        instruction=str(tool_args.get("instruction") or ""),
        worker_id=str(tool_args.get("worker_id") or ""),
        backend=str(tool_args.get("backend") or ""),
        priority=tool_args.get("priority"),
        metadata=_dispatch_metadata_from_runtime(dispatcher, tool_args),
    )
    if dispatcher.task_inbox_id:
        dispatched_worker_id = str(result.get("worker_id") or "").strip()
        if dispatched_worker_id:
            try:
                await task_inbox.assign_worker(
                    dispatcher.task_inbox_id,
                    worker_id=dispatched_worker_id,
                    reason=str(result.get("selection_reason") or ""),
                    manager_id="core-manager",
                )
            except Exception:
                pass
    if dispatcher.on_worker_dispatched is not None:
        dispatcher.on_worker_dispatched(
            str(result.get("worker_id") or "").strip(),
            str(result.get("worker_name") or "").strip(),
        )
    return result


async def _worker_status_handler(
    dispatcher: Any,
    tool_args: Dict[str, Any],
) -> Dict[str, Any]:
    _ = dispatcher
    return await dispatch_tools.worker_status(
        worker_id=str(tool_args.get("worker_id") or ""),
        limit=int(tool_args.get("limit", 10) or 10),
    )


async def _gh_cli_handler(
    dispatcher: Any,
    tool_args: Dict[str, Any],
) -> Dict[str, Any]:
    notify_target = _notify_target_from_dispatcher(dispatcher)
    return await gh_tools.gh_cli(
        action=str(tool_args.get("action") or "auth_status"),
        hostname=str(tool_args.get("hostname") or "github.com"),
        scopes=tool_args.get("scopes"),
        argv=_normalize_cli_argv(tool_args.get("argv") or tool_args.get("command")),
        cwd=str(tool_args.get("cwd") or ""),
        timeout_sec=tool_args.get("timeout_sec", 120),
        notify_platform=notify_target["notify_platform"],
        notify_chat_id=notify_target["notify_chat_id"],
        notify_user_id=notify_target["notify_user_id"],
    )


async def _repo_workspace_handler(
    dispatcher: Any,
    tool_args: Dict[str, Any],
) -> Dict[str, Any]:
    _ = dispatcher
    return await repo_workspace_tools.repo_workspace(
        action=str(tool_args.get("action") or "prepare"),
        workspace_id=str(tool_args.get("workspace_id") or ""),
        repo_url=str(tool_args.get("repo_url") or ""),
        repo_path=str(tool_args.get("repo_path") or ""),
        repo_root=str(tool_args.get("repo_root") or tool_args.get("cwd") or ""),
        base_branch=str(tool_args.get("base_branch") or ""),
        branch_name=str(tool_args.get("branch_name") or ""),
        mode=str(tool_args.get("mode") or "fresh_worktree"),
        force=tool_args.get("force", True),
    )


async def _codex_session_handler(
    dispatcher: Any,
    tool_args: Dict[str, Any],
) -> Dict[str, Any]:
    user_request = dispatcher._extract_user_request()
    return await codex_tools.codex_session(
        action=str(tool_args.get("action") or "status"),
        session_id=str(tool_args.get("session_id") or ""),
        workspace_id=str(tool_args.get("workspace_id") or ""),
        cwd=str(tool_args.get("cwd") or ""),
        instruction=str(tool_args.get("instruction") or user_request),
        user_reply=str(
            tool_args.get("user_reply") or tool_args.get("instruction") or ""
        ),
        backend=str(tool_args.get("backend") or "codex"),
        timeout_sec=tool_args.get("timeout_sec", 2400),
        source=str(tool_args.get("source") or ""),
        skill_name=str(tool_args.get("skill_name") or ""),
    )


async def _git_ops_handler(
    dispatcher: Any,
    tool_args: Dict[str, Any],
) -> Dict[str, Any]:
    _ = dispatcher
    return await git_tools.git_ops(
        action=str(tool_args.get("action") or "status"),
        workspace_id=str(tool_args.get("workspace_id") or ""),
        repo_root=str(tool_args.get("repo_root") or tool_args.get("cwd") or ""),
        mode=str(tool_args.get("mode") or "working"),
        base_branch=str(tool_args.get("base_branch") or ""),
        message=str(tool_args.get("message") or tool_args.get("commit_message") or ""),
        strategy=str(tool_args.get("strategy") or "auto"),
        branch_name=str(tool_args.get("branch_name") or ""),
        owner=str(tool_args.get("owner") or ""),
        repo=str(tool_args.get("repo") or ""),
    )


skill_tool_handler_registry = SkillToolHandlerRegistry()
skill_tool_handler_registry.register(
    "manager.worker_management.list",
    _list_workers_handler,
)
skill_tool_handler_registry.register(
    "manager.worker_management.dispatch",
    _dispatch_worker_handler,
)
skill_tool_handler_registry.register(
    "manager.worker_management.status",
    _worker_status_handler,
)
skill_tool_handler_registry.register(
    "manager.gh_cli",
    _gh_cli_handler,
)
skill_tool_handler_registry.register(
    "manager.repo_workspace",
    _repo_workspace_handler,
)
skill_tool_handler_registry.register(
    "manager.codex_session",
    _codex_session_handler,
)
skill_tool_handler_registry.register(
    "manager.git_ops",
    _git_ops_handler,
)
