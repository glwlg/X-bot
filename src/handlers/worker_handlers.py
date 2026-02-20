import asyncio
import logging
import re
import shlex
from datetime import datetime

from core.heartbeat_store import heartbeat_store
from core.platform.models import UnifiedContext
from core.tool_access_store import tool_access_store
from core.worker_runtime import worker_runtime
from core.worker_store import worker_registry, worker_task_store
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
        "`/worker auth <codex|gemini-cli> <start|status>`\n"
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
    """Manage userland workers: /worker [list|create|use|run|delete|auth|tasks]."""
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
            result = await worker_runtime.execute_task(
                worker_id=worker_id,
                source="user_cmd",
                instruction=instruction,
                backend=selected_backend,
                metadata={
                    "platform": ctx.message.platform,
                    "chat_id": ctx.message.chat.id,
                    "user_id": str(user_id),
                    "force_shell": force_shell,
                },
            )

            fallback_used = False
            if (
                not result.get("ok")
                and not (force_shell or inferred_shell)
                and str(selected_backend).strip().lower()
                in {"codex", "gemini", "gemini-cli"}
                and str(result.get("error", ""))
                in {"cli_not_found", "exec_prepare_failed"}
                and _looks_like_shell_command(instruction)
            ):
                fallback = await worker_runtime.execute_task(
                    worker_id=worker_id,
                    source="user_cmd",
                    instruction=instruction,
                    backend="shell",
                    metadata={
                        "platform": ctx.message.platform,
                        "chat_id": ctx.message.chat.id,
                        "user_id": str(user_id),
                        "fallback_from_backend": str(selected_backend),
                    },
                )
                if fallback.get("ok"):
                    result = fallback
                    fallback_used = True

            if result.get("ok"):
                prefix = "✅ Worker 执行完成"
                if fallback_used:
                    prefix = "✅ Worker 执行完成（已自动降级到 shell backend）"
                await ctx.reply(
                    f"{prefix}\n"
                    f"- task_id: `{result.get('task_id')}`\n"
                    f"- backend: `{result.get('backend')}`\n\n"
                    f"{result.get('summary') or ''}"
                )
            else:
                await ctx.reply(
                    f"❌ Worker 执行失败\n"
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
        rows = await worker_task_store.list_recent(
            worker_id=active,
            limit=10,
            include_sources=include_sources,
            exclude_sources=exclude_sources,
        )
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
                    f"- `{row.get('task_id')}` | {row.get('status')} | {str(row.get('source'))} "
                    f"| retry={int(row.get('retry_count', 0) or 0)} "
                    f"| {str(row.get('result_summary') or '')[:100]}"
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
        parts = args.split()
        if len(parts) < 2:
            await ctx.reply("用法: `/worker auth <codex|gemini-cli> <start|status>`")
            return
        provider = parts[0].strip().lower()
        action = parts[1].strip().lower()
        if provider == "gemini":
            provider = "gemini-cli"
        if provider not in {"codex", "gemini-cli"}:
            await ctx.reply("只支持 `codex` 或 `gemini-cli`。")
            return

        worker_id = await _resolve_active_worker(user_id)
        worker = await worker_registry.get_worker(worker_id)
        if not worker:
            await ctx.reply("❌ 当前 worker 无效。")
            return

        auth_state = dict((worker.get("auth") or {}).get(provider) or {})
        if action == "start":
            manual = await worker_runtime.build_auth_start_command(worker_id, provider)
            if not manual.get("ok"):
                await ctx.reply(
                    "❌ 无法生成授权命令\n"
                    f"- worker: `{worker_id}`\n"
                    f"- provider: `{provider}`\n"
                    f"- error: `{manual.get('error', 'unknown')}`"
                )
                return

            auth_state = {
                "status": "pending",
                "provider": provider,
                "runtime_mode": manual.get("runtime_mode", ""),
                "workspace_root": manual.get("workspace_root", ""),
                "manual_command": manual.get("command", ""),
                "last_update": datetime.now()
                .astimezone()
                .isoformat(timespec="seconds"),
                "method": "manual_cli_login",
            }
            await worker_registry.set_auth_state(worker_id, provider, auth_state)
            await ctx.reply(
                "🔐 请在宿主机终端执行以下命令完成登录\n"
                f"- worker: `{worker_id}`\n"
                f"- provider: `{provider}`\n"
                f"- runtime_mode: `{manual.get('runtime_mode', '')}`\n\n"
                f"```bash\n{manual.get('command', '')}\n```"
            )
            return

        if action == "status":
            status_result = await worker_runtime.check_auth_status(worker_id, provider)
            status = status_result.get("status", "unknown")
            auth_state = {
                **auth_state,
                "status": status,
                "provider": provider,
                "runtime_mode": status_result.get("runtime_mode", ""),
                "last_exit_code": status_result.get("exit_code"),
                "last_summary": status_result.get("summary", ""),
                "last_error": status_result.get("error", ""),
                "last_update": datetime.now()
                .astimezone()
                .isoformat(timespec="seconds"),
            }
            await worker_registry.set_auth_state(worker_id, provider, auth_state)
            await ctx.reply(
                "🔎 授权状态\n"
                f"- worker: `{worker_id}`\n"
                f"- provider: `{provider}`\n"
                f"- status: `{status}`\n"
                f"- runtime_mode: `{status_result.get('runtime_mode', '')}`\n"
                f"- authenticated: `{status_result.get('authenticated', False)}`\n"
                f"- exit_code: `{status_result.get('exit_code', '')}`\n\n"
                f"{status_result.get('summary', '')}"
            )
            return

        await ctx.reply("用法: `/worker auth <codex|gemini-cli> <start|status>`")
        return

    await ctx.reply(_worker_usage_text())
