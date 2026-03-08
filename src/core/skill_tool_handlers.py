from __future__ import annotations

import re
from typing import Any, Awaitable, Callable, Dict

from core.task_inbox import task_inbox
from core.tools.dev_tools import dev_tools
from core.tools.dispatch_tools import dispatch_tools
from manager.integrations.github_client import parse_repo_slug


SkillToolHandler = Callable[[Any, Dict[str, Any]], Awaitable[Dict[str, Any]]]


_GITHUB_URL_PATTERN = re.compile(r"https?://github\.com/[^\s)\"'>]+", re.IGNORECASE)


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
    return metadata_obj


def _extract_first_github_url(text: str) -> str:
    match = _GITHUB_URL_PATTERN.search(str(text or "").strip())
    if not match:
        return ""
    return str(match.group(0) or "").strip().rstrip(".,)")


def _derive_skill_name(*, tool_args: Dict[str, Any], user_request: str) -> str:
    explicit = str(tool_args.get("skill_name") or "").strip()
    if explicit:
        return explicit

    repo_url = str(tool_args.get("repo_url") or "").strip() or _extract_first_github_url(
        user_request
    )
    owner = str(tool_args.get("owner") or "").strip()
    repo = str(tool_args.get("repo") or "").strip()
    if owner and repo:
        return repo
    if repo_url:
        _owner, resolved_repo = parse_repo_slug(repo_url)
        if resolved_repo:
            return resolved_repo
    return ""


def _looks_like_external_skill_integration(
    *,
    requested_action: str,
    tool_args: Dict[str, Any],
    user_request: str,
) -> bool:
    action = str(requested_action or "").strip().lower()
    if action not in {"", "run", "plan", "skill_create", "skill_template"}:
        return False
    if str(tool_args.get("source") or "").strip() == "manual_install_after_coding":
        return False

    repo_url = str(tool_args.get("repo_url") or "").strip() or _extract_first_github_url(
        user_request
    )
    owner = str(tool_args.get("owner") or "").strip()
    repo = str(tool_args.get("repo") or "").strip()
    raw = " ".join(
        [
            str(user_request or ""),
            str(tool_args.get("requirement") or ""),
            str(tool_args.get("instruction") or ""),
        ]
    ).lower()
    skill_tokens = ("skill", "技能", "阿黑", "worker")
    integration_tokens = ("集成", "安装", "接入", "给阿黑用", "让阿黑用", "adopt", "install", "integrate")

    has_repo_ref = bool(repo_url) or bool(owner and repo)
    return has_repo_ref and any(token in raw for token in skill_tokens) and any(
        token in raw for token in integration_tokens
    )


def _software_delivery_notify_target(dispatcher: Any) -> Dict[str, str]:
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


async def _software_delivery_handler(
    dispatcher: Any,
    tool_args: Dict[str, Any],
) -> Dict[str, Any]:
    user_request = dispatcher._extract_user_request()
    repo_url = str(tool_args.get("repo_url") or "").strip() or _extract_first_github_url(
        user_request
    )
    requested_action = dispatcher._infer_software_delivery_action(
        requested_action=str(tool_args.get("action") or "run"),
        user_request=user_request,
        args={**dict(tool_args), "repo_url": repo_url},
    )
    requested_requirement = str(tool_args.get("requirement") or "")
    requested_instruction = str(tool_args.get("instruction") or "")
    resolved_skill_name = _derive_skill_name(tool_args=tool_args, user_request=user_request)
    notify_target = _software_delivery_notify_target(dispatcher)

    if _looks_like_external_skill_integration(
        requested_action=requested_action,
        tool_args=tool_args,
        user_request=user_request,
    ):
        requested_action = "skill_create"

    return await dev_tools.software_delivery(
        action=requested_action,
        task_id=str(tool_args.get("task_id") or ""),
        requirement=requested_requirement or user_request,
        instruction=requested_instruction or requested_requirement or user_request,
        issue=str(tool_args.get("issue") or ""),
        repo_path=str(tool_args.get("repo_path") or ""),
        repo_url=repo_url,
        cwd=str(tool_args.get("cwd") or ""),
        skill_name=resolved_skill_name,
        source=str(tool_args.get("source") or ""),
        template_kind=str(tool_args.get("template_kind") or ""),
        owner=str(tool_args.get("owner") or ""),
        repo=str(tool_args.get("repo") or ""),
        backend=str(tool_args.get("backend") or ""),
        branch_name=str(tool_args.get("branch_name") or ""),
        base_branch=str(tool_args.get("base_branch") or ""),
        commit_message=str(tool_args.get("commit_message") or ""),
        pr_title=str(tool_args.get("pr_title") or ""),
        pr_body=str(tool_args.get("pr_body") or ""),
        timeout_sec=tool_args.get("timeout_sec", 1800),
        validation_commands=tool_args.get("validation_commands"),
        auto_publish=tool_args.get("auto_publish", True),
        auto_push=tool_args.get("auto_push", True),
        auto_pr=tool_args.get("auto_pr", True),
        target_service=str(tool_args.get("target_service") or ""),
        rollout=str(tool_args.get("rollout") or ""),
        validate_only=tool_args.get("validate_only", False),
        notify_platform=notify_target["notify_platform"],
        notify_chat_id=notify_target["notify_chat_id"],
        notify_user_id=notify_target["notify_user_id"],
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
    "manager.software_delivery",
    _software_delivery_handler,
)
