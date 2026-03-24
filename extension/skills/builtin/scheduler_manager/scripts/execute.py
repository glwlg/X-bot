from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
SRC_ROOT = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.channel_access import channel_feature_denied_text, is_channel_feature_enabled
from core.platform.models import UnifiedContext
from core.skill_menu import make_callback, parse_callback
from core.skill_cli import (
    add_common_arguments,
    merge_params,
    prepare_default_env,
    run_execute_cli,
)

prepare_default_env(REPO_ROOT)

from core.state_store import (
    add_scheduled_task,
    delete_task,
    get_all_active_tasks,
    update_task_delivery_target,
)
import logging

logger = logging.getLogger(__name__)
SCHEDULE_MENU_NS = "schm"


def _scheduler_enabled(ctx: UnifiedContext) -> bool:
    message = getattr(ctx, "message", None)
    platform = str(getattr(message, "platform", "") or "").strip().lower()
    user = getattr(message, "user", None)
    platform_user_id = str(getattr(user, "id", "") or "").strip()
    return is_channel_feature_enabled(
        platform=platform,
        platform_user_id=platform_user_id,
        feature="scheduler",
    )


def _format_delivery_target(task: dict) -> str:
    platform = str(task.get("platform") or "").strip()
    chat_id = str(task.get("chat_id") or "").strip()
    if not platform or not chat_id:
        return "未设置"
    return f"{platform}:{chat_id}"


def _current_delivery_target(ctx: UnifiedContext) -> tuple[str, str]:
    platform = (
        str(getattr(getattr(ctx, "message", None), "platform", "") or "").strip()
        or "telegram"
    )
    chat_id = str(
        getattr(getattr(getattr(ctx, "message", None), "chat", None), "id", "") or ""
    ).strip()
    return platform, chat_id


def _current_session_id(ctx: UnifiedContext) -> str:
    user_data = getattr(ctx, "user_data", None)
    if not isinstance(user_data, dict):
        return ""
    return str(user_data.get("current_session_id") or "").strip()


def _parse_schedule_subcommand(text: str) -> tuple[str, str]:
    raw = str(text or "").strip()
    if not raw:
        return "menu", ""

    parts = raw.split(maxsplit=2)
    if not parts:
        return "list", ""
    if not parts[0].startswith("/schedule"):
        return "help", ""
    if len(parts) == 1:
        return "menu", ""

    sub = str(parts[1] or "").strip().lower()
    args = str(parts[2] if len(parts) >= 3 else "").strip()

    if sub in {"menu", "home", "start"}:
        return "menu", ""
    if sub in {"list", "ls", "show"}:
        return "list", ""
    if sub in {"delete", "del", "rm", "remove"}:
        return "delete", args
    if sub in {"help", "h", "?"}:
        return "help", ""
    return "help", ""


def _schedule_usage_text() -> str:
    return (
        "用法:\n"
        "`/schedule`\n"
        "`/schedule list`\n"
        "`/schedule delete <task_id>`\n"
        "`/schedule help`\n\n"
        "新增任务请直接告诉我任务内容，或通过技能参数创建。"
    )


def _schedule_menu_ui() -> dict:
    return {
        "actions": [
            [
                {"text": "📋 任务列表", "callback_data": make_callback(SCHEDULE_MENU_NS, "list")},
                {"text": "❌ 删除任务", "callback_data": make_callback(SCHEDULE_MENU_NS, "delete")},
            ],
            [
                {"text": "📍 当前聊天设为推送渠道", "callback_data": make_callback(SCHEDULE_MENU_NS, "bindhelp")},
                {"text": "➕ 新建说明", "callback_data": make_callback(SCHEDULE_MENU_NS, "addhelp")},
            ],
            [
                {"text": "ℹ️ 帮助", "callback_data": make_callback(SCHEDULE_MENU_NS, "help")},
            ],
        ]
    }


async def show_schedule_menu(ctx: UnifiedContext) -> dict:
    tasks = await get_all_active_tasks()
    return {
        "text": (
            "⏰ **定时任务管理**\n\n"
            f"当前活跃任务：{len(tasks)}\n\n"
            "删除任务可直接用 `/schedule delete <task_id>`。\n"
            "新增任务默认推送到创建它的聊天。\n"
            "如果要改某个任务的推送渠道，请在目标聊天里打开菜单后点对应任务的「📍 当前聊天」。"
        ),
        "ui": _schedule_menu_ui(),
    }


def _schedule_add_help_response() -> dict:
    return {
        "text": (
            "➕ **新增定时任务**\n\n"
            "这个命令入口当前只负责查看和删除。\n"
            "如果你想新建任务，建议直接描述需求，例如：\n"
            "• 每天早上 9 点提醒我看日报\n"
            "• 每小时检查一次 RSS\n\n"
            "如果要手动删除，直接发 `/schedule delete <task_id>`。"
        ),
        "ui": {
            "actions": [
                [
                    {"text": "🏠 返回首页", "callback_data": make_callback(SCHEDULE_MENU_NS, "home")},
                    {"text": "📋 查看任务", "callback_data": make_callback(SCHEDULE_MENU_NS, "list")},
                ]
            ]
        },
    }


async def execute(ctx: UnifiedContext, params: dict, runtime=None) -> dict:
    """
    Execute scheduler management operations.
    """
    if not _scheduler_enabled(ctx):
        return {"text": channel_feature_denied_text("scheduler"), "ui": {}}
    action = params.get("action", "list")
    user_id = ctx.message.user.id if ctx.message and ctx.message.user else "0"
    platform = (
        ctx.message.platform if ctx.message and ctx.message.platform else "telegram"
    )

    if action == "add":
        crontab = params.get("crontab")
        instruction = params.get("instruction")
        # Default True if not specified as 'false' string
        push_param = str(params.get("push", "true")).lower()
        need_push = push_param == "true" or push_param == "1"

        if not instruction:
            return {"text": "❌ 请提供 `instruction`"}

        try:
            task_id = await add_scheduled_task(
                crontab,
                instruction,
                user_id,
                platform,
                chat_id=str(getattr(ctx.message.chat, "id", "") or "").strip(),
                session_id=_current_session_id(ctx),
                need_push=need_push,
            )

            # 立即触发 Scheduler 重载
            from core.scheduler import reload_scheduler_jobs

            await reload_scheduler_jobs()

            return {
                "text": (
                    f"✅ 定时任务已添加 (ID: {task_id})\n"
                    f"Cron: `{crontab}`\n"
                    f"Instruction: `{instruction}`\n"
                    f"Push: `{'Yes' if need_push else 'No'}`\n"
                    f"状态: 已立即生效"
                )
            }
        except Exception as e:
            return {"text": f"❌ 添加失败: {e}"}

    elif action == "list":
        return await list_tasks_command(ctx)

    elif action == "delete":
        task_id = params.get("task_id")
        if not task_id:
            return {"text": "❌ 请提供 `task_id`"}

        try:
            await delete_task(int(task_id))
            from core.scheduler import reload_scheduler_jobs

            await reload_scheduler_jobs()
            return {"text": f"✅ 任务 {task_id} 已删除并立即生效。"}
        except Exception as e:
            return {"text": f"❌ 删除失败: {e}"}

    return {"text": f"❌ 未知操作: {action}"}


def register_handlers(adapter_manager):
    """注册 Scheduler 二级命令和 Callback"""
    from core.config import is_user_allowed

    async def cmd_schedule(ctx):
        if not await is_user_allowed(ctx.message.user.id):
            return
        if not _scheduler_enabled(ctx):
            return {"text": channel_feature_denied_text("scheduler"), "ui": {}}

        sub, args = _parse_schedule_subcommand(ctx.message.text or "")
        if sub == "menu":
            return await show_schedule_menu(ctx)
        if sub == "list":
            return await list_tasks_command(ctx, include_menu_nav=True)

        if sub == "delete":
            task_id_raw = args.strip()
            if not task_id_raw:
                return await show_delete_menu(ctx, include_menu_nav=True)
            try:
                task_id = int(task_id_raw)
            except ValueError:
                return "❌ 任务 ID 必须是数字。"

            try:
                await delete_task(task_id)
                from core.scheduler import reload_scheduler_jobs

                await reload_scheduler_jobs()
                return f"✅ 任务 {task_id} 已删除。"
            except Exception as exc:
                return f"❌ 删除失败: {exc}"

        return _schedule_usage_text()

    adapter_manager.on_command("schedule", cmd_schedule, description="管理定时任务")

    # Callbacks
    adapter_manager.on_callback_query("^sch_del_", handle_task_delete_callback)
    adapter_manager.on_callback_query("^sch_route_", handle_task_delete_callback)
    adapter_manager.on_callback_query("^schm_", handle_task_delete_callback)


async def list_tasks_command(
    ctx: UnifiedContext,
    *,
    include_menu_nav: bool = False,
):
    """处理 schedule 列表展示，显示带按钮的任务列表"""
    tasks = await get_all_active_tasks()

    if not tasks:
        # return {"text": "📭 当前没有活跃的定时任务。", "ui": {}}
        # Skill execute return expectation can be str or dict with text/ui
        # But here we are called by execute or cmd_tasks.
        # execute handles dict return nicely? execute implementation above returns directly.
        # Let's return dict format which is supported by unified_adapter for skills usually,
        # but check how cmd handles it.
        # The adapter generally handles str or dict.
        return {
            "text": "📭 当前没有活跃的定时任务。",
            "ui": _schedule_menu_ui() if include_menu_nav else {},
        }

    msg = "📋 **定时任务列表**\n\n"
    all_sorted = list(tasks)

    for t in all_sorted:
        msg += f"🕒 **ID: {t['id']}**\n"
        msg += f"   Cron: `{t['crontab']}`\n"
        msg += f"   Desc: `{t['instruction']}`\n"
        msg += f"   Push: {t.get('need_push', True)}\n\n"
        msg += f"   Channel: `{_format_delivery_target(t)}`\n\n"

    # Actions: Create delete buttons for own tasks (or all if admin?)
    # Assuming user can delete any task for now as per previous logic "trust SkillAgent"
    # But for UI clutter, maybe just first few or allow all.
    # Let's create actions for ALL tasks for now.

    actions = []
    temp_row = []
    for t in all_sorted:
        instr_short = (
            t["instruction"][:8] + ".."
            if len(t["instruction"]) > 8
            else t["instruction"]
        )
        temp_row.append(
            {
                "text": f"❌ {t['id']} {instr_short}",
                "callback_data": f"sch_del_{t['id']}",
            }
        )
        temp_row.append(
            {
                "text": f"📍 {t['id']} 当前聊天",
                "callback_data": f"sch_route_{t['id']}",
            }
        )
        if len(temp_row) == 2:
            actions.append(temp_row)
            temp_row = []

    if temp_row:
        actions.append(temp_row)

    if include_menu_nav:
        actions.append(
            [
                {"text": "➕ 新建说明", "callback_data": make_callback(SCHEDULE_MENU_NS, "addhelp")},
                {"text": "🏠 返回首页", "callback_data": make_callback(SCHEDULE_MENU_NS, "home")},
            ]
        )

    return {"text": msg, "ui": {"actions": actions}}


async def show_delete_menu(
    ctx: UnifiedContext,
    *,
    include_menu_nav: bool = False,
):
    """显示删除菜单"""
    return await list_tasks_command(ctx, include_menu_nav=include_menu_nav)


async def handle_task_delete_callback(ctx: UnifiedContext):
    """处理删除按钮回调"""
    if not _scheduler_enabled(ctx):
        await ctx.reply(channel_feature_denied_text("scheduler"))
        return
    data = ctx.callback_data
    if not data:
        return

    action, _parts = parse_callback(data, SCHEDULE_MENU_NS)
    if action:
        await ctx.answer_callback()
        if action == "home":
            payload = await show_schedule_menu(ctx)
        elif action == "list":
            payload = await list_tasks_command(ctx, include_menu_nav=True)
        elif action == "delete":
            payload = await show_delete_menu(ctx, include_menu_nav=True)
        elif action == "bindhelp":
            payload = {
                "text": (
                    "📍 **定时任务推送渠道**\n\n"
                    "每个定时任务都能单独设置推送渠道。\n"
                    "默认会发到创建该任务的聊天。\n\n"
                    "如果你想改成当前聊天，请先点「📋 任务列表」，再点对应任务的「📍 当前聊天」。"
                ),
                "ui": {
                    "actions": [
                        [
                            {"text": "📋 任务列表", "callback_data": make_callback(SCHEDULE_MENU_NS, "list")},
                            {"text": "🏠 返回首页", "callback_data": make_callback(SCHEDULE_MENU_NS, "home")},
                        ]
                    ]
                },
            }
        elif action == "addhelp":
            payload = _schedule_add_help_response()
        elif action == "help":
            payload = {
                "text": _schedule_usage_text(),
                "ui": {
                    "actions": [
                        [
                            {"text": "🏠 返回首页", "callback_data": make_callback(SCHEDULE_MENU_NS, "home")},
                            {"text": "📋 查看任务", "callback_data": make_callback(SCHEDULE_MENU_NS, "list")},
                        ]
                    ]
                },
            }
        else:
            payload = {"text": "❌ 未知操作。", "ui": _schedule_menu_ui()}
        await ctx.edit_message(ctx.message.id, payload["text"], ui=payload.get("ui"))
        return

    await ctx.answer_callback()

    if data.startswith("sch_route_"):
        try:
            task_id = int(data.replace("sch_route_", ""))
        except ValueError:
            return "❌ 无效的操作。"

        from core.scheduler import reload_scheduler_jobs

        current_platform, current_chat_id = _current_delivery_target(ctx)
        changed = await update_task_delivery_target(
            task_id,
            platform=current_platform,
            chat_id=current_chat_id,
            session_id=_current_session_id(ctx),
        )
        if not changed:
            return "❌ 任务不存在。"
        await reload_scheduler_jobs()

        payload = await list_tasks_command(ctx, include_menu_nav=True)
        text = (
            f"✅ 已把任务 {task_id} 的推送渠道改为当前聊天 "
            f"`{current_platform}:{current_chat_id}`。\n\n{payload['text']}"
        )
        await ctx.edit_message(ctx.message.id, text, ui=payload.get("ui"))
        return None

    try:
        task_id = int(data.replace("sch_del_", ""))
    except ValueError:
        return "❌ 无效的操作。"

    try:
        await delete_task(task_id)
        from core.scheduler import reload_scheduler_jobs

        await reload_scheduler_jobs()

        payload = await list_tasks_command(ctx, include_menu_nav=True)
        text = f"✅ 任务 {task_id} 已删除。\n\n{payload['text']}"
        await ctx.edit_message(ctx.message.id, text, ui=payload.get("ui"))
        return None
    except Exception as e:
        return f"❌ 删除失败: {e}"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scheduler manager skill CLI bridge.",
    )
    add_common_arguments(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Create a scheduled task")
    add_parser.add_argument("--crontab", required=True, help="Cron expression")
    add_parser.add_argument(
        "--instruction",
        required=True,
        help="Instruction to run on schedule",
    )
    add_parser.add_argument(
        "--push",
        choices=("true", "false"),
        default="true",
        help="Whether to push task results",
    )

    subparsers.add_parser("list", help="List active tasks")

    delete_parser = subparsers.add_parser("delete", help="Delete a task")
    delete_parser.add_argument("task_id", help="Task id to delete")
    return parser


def _params_from_args(args: argparse.Namespace) -> dict:
    command = str(args.command or "").strip().lower()
    if command == "add":
        return merge_params(
            args,
            {
                "action": "add",
                "crontab": str(args.crontab or "").strip(),
                "instruction": str(args.instruction or "").strip(),
                "push": str(args.push or "true").strip().lower(),
            },
        )
    if command == "list":
        return merge_params(args, {"action": "list"})
    if command == "delete":
        return merge_params(
            args,
            {"action": "delete", "task_id": str(args.task_id or "").strip()},
        )
    raise SystemExit(f"unsupported command: {command}")


async def _run() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return await run_execute_cli(execute, args=args, params=_params_from_args(args))


from core.extension_base import SkillExtension


class SchedulerManagerSkillExtension(SkillExtension):
    name = "scheduler_manager_extension"
    skill_name = "scheduler_manager"

    def register(self, runtime) -> None:
        register_handlers(runtime.adapter_manager)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
