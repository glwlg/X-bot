import asyncio
import logging
import re
import shlex

from core.heartbeat_store import heartbeat_store
from core.platform.models import UnifiedContext
from core.tool_access_store import tool_access_store
from core.tools.dispatch_tools import dispatch_tools
from core.worker_store import worker_registry
from shared.queue.dispatch_queue import dispatch_queue
from .base_handlers import check_permission_unified

logger = logging.getLogger(__name__)

SHELL_COMMAND_HINTS = {
    "echo",
    "ls",
    "pwd",
    "cat",
    "head",
    "tail",
    "grep",
    "rg",
    "find",
    "git",
    "docker",
    "uv",
    "python",
    "python3",
    "pip",
    "npm",
    "pnpm",
    "yarn",
    "bash",
    "sh",
    "zsh",
    "curl",
    "wget",
    "make",
    "pytest",
}


def _looks_like_shell_command(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw or "\n" in raw:
        return False
    try:
        parts = shlex.split(raw)
    except Exception:
        return False
    if not parts:
        return False
    first = parts[0]
    if first in SHELL_COMMAND_HINTS:
        return True
    if first.startswith("./") or first.startswith("/") or first.startswith("../"):
        return True
    return False


def _parse_subcommand(text: str) -> tuple[str, str]:
    raw = (text or "").strip()
    if not raw:
        return "help", ""
    parts = raw.split(maxsplit=2)
    if not parts:
        return "help", ""
    if not parts[0].startswith("/worker"):
        return "help", ""
    if len(parts) == 1:
        return "list", ""
    cmd = parts[1].strip().lower()
    args = parts[2].strip() if len(parts) >= 3 else ""
    return cmd, args


def _parse_tasks_filters(raw_args: str) -> tuple[list[str] | None, list[str] | None]:
    raw = str(raw_args or "").strip().lower()
    if not raw:
        # OBS-002 default view: focus on end-user chat tasks.
        return ["user_chat"], ["heartbeat"]
    if raw in {"all", "--all"}:
        return None, None

    include: list[str] = []
    exclude: list[str] = []
    for part in raw.split():
        token = part.strip()
        if not token:
            continue
        if token.startswith("source="):
            values = token.split("=", 1)[1]
            include.extend([item.strip() for item in values.split(",") if item.strip()])
            continue
        if token.startswith("exclude="):
            values = token.split("=", 1)[1]
            exclude.extend([item.strip() for item in values.split(",") if item.strip()])
            continue
        if token.startswith("only="):
            values = token.split("=", 1)[1]
            include.extend([item.strip() for item in values.split(",") if item.strip()])
            continue

    if not include:
        include = ["user_chat"]
    return include, exclude or ["heartbeat"]


def _extract_policy_tokens(
    raw_args: str,
) -> tuple[str, list[str] | None, list[str] | None]:
    tokens = [item.strip() for item in str(raw_args or "").split() if item.strip()]
    worker_id = ""
    allow: list[str] | None = None
    deny: list[str] | None = None
    for idx, token in enumerate(tokens):
        lowered = token.lower()
        if lowered.startswith("allow="):
            values = lowered.split("=", 1)[1]
            allow = [item.strip() for item in values.split(",") if item.strip()]
            continue
        if lowered.startswith("deny="):
            values = lowered.split("=", 1)[1]
            deny = [item.strip() for item in values.split(",") if item.strip()]
            continue
        if idx == 0 and "=" not in token:
            worker_id = token
    return worker_id, allow, deny


def _format_policy(policy: dict) -> str:
    tools_cfg = dict((policy or {}).get("tools") or {})
    allow = tools_cfg.get("allow") or []
    deny = tools_cfg.get("deny") or []
    return f"allow={allow}\ndeny={deny}"


def _worker_usage_text() -> str:
    return (
        "用法:\n"
        "`/worker list`\n"
        "`/worker create <name> [allow=group:all deny=group:coding]`\n"
        "`/worker use <worker_id>`\n"
        "`/worker backend <worker_id> <core-agent|codex|gemini-cli|shell>`\n"
        "`/worker run <instruction>`\n"
        "`/worker run --shell <command>`\n"
        "`/worker tasks`（默认只看 user_chat，排除 heartbeat）\n"
        "`/worker tasks all`\n"
        "`/worker tasks source=user_cmd,user_chat exclude=heartbeat`\n"
        "`/worker tools groups`\n"
        "`/worker tools <worker_id>`\n"
        "`/worker tools <worker_id> allow=group:all deny=group:coding`\n"
        "`/worker tools <worker_id> reset`\n"
        "`/worker delete <worker_id>`\n"
        "`/worker help`"
    )


async def _resolve_active_worker(user_id: str) -> str:
    worker_id = await heartbeat_store.get_active_worker_id(user_id)
    if worker_id:
        exists = await worker_registry.get_worker(worker_id)
        if exists:
            return worker_id
    default = await worker_registry.ensure_default_worker()
    await heartbeat_store.set_active_worker_id(user_id, str(default["id"]))
    return str(default["id"])


async def worker_command(ctx: UnifiedContext) -> None:
    if not await check_permission_unified(ctx):
        return

    user_id = str(ctx.message.user.id)
    text = ctx.message.text or ""
    sub, args = _parse_subcommand(text)

    if sub in {"help", "h", "?"}:
        await ctx.reply(_worker_usage_text())
        return

    if sub in {"list", "ls"}:
        await worker_registry.ensure_default_worker()
        workers = await worker_registry.list_workers()
        active_worker = await _resolve_active_worker(user_id)
        if not workers:
            await ctx.reply("当前没有 worker，使用 `/worker create <name>` 创建。")
            return
        lines = ["🧩 Workers"]
        for item in workers:
            marker = "👉" if str(item.get("id")) == active_worker else "  "
            policy = tool_access_store.get_worker_policy(
                str(item.get("id") or "worker-main")
            )
            tools_cfg = dict(policy.get("tools") or {})
            deny = tools_cfg.get("deny") or []
            lines.append(
                f"{marker} `{item.get('id')}` | {item.get('name')} | backend={item.get('backend')} | status={item.get('status')} | deny={deny}"
            )
        await ctx.reply("\n".join(lines))
        return

    if sub == "create":
        worker_id_hint, allow_tokens, deny_tokens = _extract_policy_tokens(args)
        name = worker_id_hint or "worker"
        worker = await worker_registry.create_worker(name=name)
        if allow_tokens is not None or deny_tokens is not None:
            ok, reason = tool_access_store.set_worker_policy(
                worker["id"],
                allow=allow_tokens,
                deny=deny_tokens,
                actor="core-manager",
            )
            if not ok:
                await ctx.reply(f"⚠️ Worker 已创建，但工具策略更新失败：{reason}")
        await heartbeat_store.set_active_worker_id(user_id, str(worker["id"]))
        policy_text = _format_policy(tool_access_store.get_worker_policy(worker["id"]))
        await ctx.reply(
            f"✅ 已创建 worker: `{worker['id']}`（已设为当前会话默认）\n\n"
            f"工具策略:\n{policy_text}"
        )
        return

    if sub == "use":
        worker_id = args.strip()
        if not worker_id:
            await ctx.reply("用法: `/worker use <worker_id>`")
            return
        worker = await worker_registry.get_worker(worker_id)
        if not worker:
            await ctx.reply(f"❌ worker 不存在: `{worker_id}`")
            return
        await heartbeat_store.set_active_worker_id(user_id, str(worker["id"]))
        await ctx.reply(f"✅ 当前会话 worker 已切换为 `{worker['id']}`")
        return

    if sub == "delete":
        worker_id = args.strip()
        if not worker_id:
            await ctx.reply("用法: `/worker delete <worker_id>`")
            return
        if worker_id in {"worker-main", "main"}:
            await ctx.reply("❌ 默认 worker 不允许删除。")
            return
        deleted = await worker_registry.delete_worker(worker_id)
        if not deleted:
            await ctx.reply(f"❌ worker 不存在: `{worker_id}`")
            return
        active = await _resolve_active_worker(user_id)
        if active == worker_id:
            default = await worker_registry.ensure_default_worker()
            await heartbeat_store.set_active_worker_id(user_id, str(default["id"]))
        await ctx.reply(f"✅ 已删除 worker `{worker_id}`（目录文件保留）。")
        return

    if sub == "run":
        from core.task_manager import task_manager

        instruction = args.strip()
        force_shell = False
        if instruction.startswith("--shell "):
            force_shell = True
            instruction = instruction[len("--shell ") :].strip()
        elif instruction == "--shell":
            await ctx.reply("用法: `/worker run --shell <shell command>`")
            return
        if not instruction:
            await ctx.reply("用法: `/worker run <instruction>`")
            return
        worker_id = await _resolve_active_worker(user_id)
        worker = await worker_registry.get_worker(worker_id)
        if not worker:
            await ctx.reply("❌ 当前 worker 不存在，请先 `/worker list` 检查。")
            return
        configured_backend = str(worker.get("backend") or "codex")
        inferred_shell = _looks_like_shell_command(instruction)
        selected_backend = (
            "shell" if (force_shell or inferred_shell) else configured_backend
        )
        await ctx.reply(f"🚀 正在由 `{worker_id}` 执行...")
        current_task = asyncio.current_task()
        if current_task is not None:
            await task_manager.register_task(
                user_id,
                current_task,
                description="Worker 命令执行",
            )
        try:
            result = await dispatch_tools.dispatch_worker(
                instruction=instruction,
                worker_id=worker_id,
                backend=selected_backend,
                source="user_cmd",
                metadata={
                    "platform": ctx.message.platform,
                    "chat_id": ctx.message.chat.id,
                    "user_id": str(user_id),
                    "force_shell": force_shell,
                },
            )
            if result.get("ok"):
                prefix = "✅ Worker 任务已派发"
                await ctx.reply(
                    f"{prefix}\n"
                    f"- task_id: `{result.get('task_id')}`\n"
                    f"- worker: `{result.get('worker_name')}`\n"
                    f"- backend: `{result.get('backend')}`\n\n"
                    "任务执行完成后会自动回传结果。"
                )
            else:
                await ctx.reply(
                    f"❌ Worker 任务派发失败\n"
                    f"- task_id: `{result.get('task_id', '')}`\n"
                    f"- error: `{result.get('error', 'unknown')}`\n\n"
                    f"{result.get('summary') or ''}"
                )
        finally:
            task_manager.unregister_task(user_id)
        return

    if sub in {"tasks", "history"}:
        active = await _resolve_active_worker(user_id)
        include_sources, exclude_sources = _parse_tasks_filters(args)
        rows_obj = await dispatch_queue.list_tasks(worker_id=active, limit=20)
        rows = []
        include_set = set(include_sources or []) if include_sources else None
        exclude_set = set(exclude_sources or []) if exclude_sources else set()
        for item in rows_obj:
            source = str(item.source or "").strip()
            if include_set is not None and source not in include_set:
                continue
            if source in exclude_set:
                continue
            rows.append(item)
            if len(rows) >= 10:
                break
        if not rows:
            await ctx.reply("当前 worker 暂无匹配任务记录。")
            return
        lines = [
            f"🧾 Worker `{active}` 最近任务"
            + (
                f"（include={','.join(include_sources or ['all'])}; exclude={','.join(exclude_sources or []) or 'none'}）"
            )
        ]
        for row in rows:
            lines.append(
                (
                    f"- `{row.task_id}` | {row.status} | {row.source} "
                    f"| retry={int(row.retry_count or 0)} "
                    f"| {str(row.error or '')[:100]}"
                )
            )
        await ctx.reply("\n".join(lines))
        return

    if sub == "tools":
        raw = str(args or "").strip()
        if raw.lower() in {"groups", "group", "list-groups"}:
            catalog = tool_access_store.get_group_catalog()
            lines = ["🧰 工具分组目录"]
            for key, desc in catalog.items():
                lines.append(f"- `{key}`: {desc}")
            await ctx.reply("\n".join(lines))
            return

        parts = [item.strip() for item in raw.split() if item.strip()]
        action = "show"
        if parts and parts[0].lower() in {"show", "reset"}:
            action = parts[0].lower()
            parts = parts[1:]

        worker_id = ""
        if parts and "=" not in parts[0]:
            worker_id = parts[0]
            parts = parts[1:]
        if not worker_id:
            worker_id = await _resolve_active_worker(user_id)

        if action == "reset":
            ok, reason = tool_access_store.reset_worker_policy(worker_id)
            if not ok:
                await ctx.reply(f"❌ 重置失败：{reason}")
                return
            policy_text = _format_policy(tool_access_store.get_worker_policy(worker_id))
            await ctx.reply(f"✅ `{worker_id}` 工具策略已重置为默认。\n\n{policy_text}")
            return

        allow_tokens: list[str] | None = None
        deny_tokens: list[str] | None = None
        for token in parts:
            lowered = token.lower()
            if lowered.startswith("allow="):
                values = lowered.split("=", 1)[1]
                allow_tokens = [
                    item.strip() for item in values.split(",") if item.strip()
                ]
            elif lowered.startswith("deny="):
                values = lowered.split("=", 1)[1]
                deny_tokens = [
                    item.strip() for item in values.split(",") if item.strip()
                ]

        if allow_tokens is not None or deny_tokens is not None:
            ok, reason = tool_access_store.set_worker_policy(
                worker_id,
                allow=allow_tokens,
                deny=deny_tokens,
                actor="core-manager",
            )
            if not ok:
                await ctx.reply(f"❌ 更新失败：{reason}")
                return
            policy_text = _format_policy(tool_access_store.get_worker_policy(worker_id))
            await ctx.reply(f"✅ `{worker_id}` 工具策略已更新。\n\n{policy_text}")
            return

        policy_text = _format_policy(tool_access_store.get_worker_policy(worker_id))
        await ctx.reply(
            f"🧰 Worker `{worker_id}` 工具策略\n\n"
            f"{policy_text}\n\n"
            "用法:\n"
            "`/worker tools groups`\n"
            "`/worker tools <worker_id>`\n"
            "`/worker tools <worker_id> allow=group:all deny=group:coding`\n"
            "`/worker tools <worker_id> reset`"
        )
        return

    if sub == "backend":
        match = re.fullmatch(
            r"([a-zA-Z0-9_\-]+)\s+(core-agent|core|codex|gemini|gemini-cli|shell|bash|sh)",
            args.strip(),
            flags=re.IGNORECASE,
        )
        if not match:
            await ctx.reply(
                "用法: `/worker backend <worker_id> <core-agent|codex|gemini-cli|shell>`"
            )
            return
        worker_id = match.group(1)
        backend = match.group(2).strip().lower()
        if backend == "core":
            backend = "core-agent"
        if backend == "gemini":
            backend = "gemini-cli"
        if backend in {"bash", "sh"}:
            backend = "shell"
        updated = await worker_registry.update_worker(worker_id, backend=backend)
        if not updated:
            await ctx.reply(f"❌ worker 不存在: `{worker_id}`")
            return
        await ctx.reply(f"✅ `{worker_id}` backend 已设置为 `{backend}`")
        return

    if sub == "auth":
        await ctx.reply(
            "`/worker auth` 已移除。Worker 认证由 Program 生命周期统一管理。"
        )
        return

    await ctx.reply(_worker_usage_text())
