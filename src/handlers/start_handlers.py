import logging
from core.channel_user_store import DEFAULT_ACCESS
from core.platform.models import UnifiedContext
from core.skill_menu import make_callback, parse_callback
from core.session_task_store import session_task_store
from core.task_cards import build_session_brief_lines
from .base_handlers import (
    check_permission_unified,
    CONVERSATION_END,
    edit_callback_message,
    get_effective_platform,
    get_effective_user_id,
    require_feature_access,
)

logger = logging.getLogger(__name__)
HOME_MENU_NS = "home"
HELP_MENU_NS = "helpm"

WELCOME_MESSAGE = (
    "👋 **欢迎使用 Ikaros！**\n\n"
    "我是您的全能 AI 助手，支持 **自然语言交互** 与 **多模态分析**。\n\n"
    "💬 **直接对话**：你可以像朋友一样跟我聊天。\n"
    "🛠️ **执行任务**：下载视频、监控股票、阅读PDF、生成播客等。\n"
    "🧬 **自我进化**：遇到不会的问题，我会尝试自己写代码解决！\n\n"
    "👇 点击下方按钮进入帮助、技能、模型和常用工具。"
)


def _access_flags(ctx: UnifiedContext) -> dict[str, bool]:
    from core.channel_access import is_channel_feature_enabled

    platform = get_effective_platform(ctx)
    user_id = get_effective_user_id(ctx)
    flags = dict(DEFAULT_ACCESS)
    if not platform or not user_id:
        return flags
    for feature in flags:
        flags[feature] = is_channel_feature_enabled(
            platform=platform,
            platform_user_id=user_id,
            feature=feature,
        )
    return flags


def get_main_menu_ui(access: dict[str, bool] | None = None) -> dict:
    flags = dict(DEFAULT_ACCESS)
    flags.update(dict(access or {}))
    actions = [
        [
            {"text": "ℹ️ 使用帮助", "callback_data": make_callback(HOME_MENU_NS, "help")},
            {"text": "🧩 Skills", "callback_data": make_callback(HOME_MENU_NS, "skills")},
        ],
        [
            {"text": "🤖 模型", "callback_data": make_callback(HOME_MENU_NS, "model")},
            {"text": "📊 用量", "callback_data": make_callback(HOME_MENU_NS, "usage")},
        ],
    ]
    third_row = [{"text": "🧾 任务", "callback_data": make_callback(HOME_MENU_NS, "task")}]
    if flags.get("heartbeat"):
        third_row.insert(
            0,
            {"text": "💓 Heartbeat", "callback_data": make_callback(HOME_MENU_NS, "heartbeat")},
        )
    actions.append(third_row)
    fourth_row = [{"text": "🔎 检索", "callback_data": make_callback(HOME_MENU_NS, "chatlog")}]
    if flags.get("accounting"):
        fourth_row.insert(
            0,
            {"text": "📚 记账", "callback_data": make_callback(HOME_MENU_NS, "accounting")},
        )
    actions.append(fourth_row)
    actions.append(
        [
            {"text": "🗜️ 压缩", "callback_data": make_callback(HOME_MENU_NS, "compact")},
        ]
    )
    return {"actions": actions}


def _help_categories_ui() -> dict:
    return {
        "actions": [
            [
                {"text": "🚀 对话与多模态", "callback_data": make_callback(HELP_MENU_NS, "chat")},
                {"text": "🧩 技能与工具", "callback_data": make_callback(HELP_MENU_NS, "skills")},
            ],
            [
                {"text": "⚙️ 模型与用量", "callback_data": make_callback(HELP_MENU_NS, "ops")},
                {"text": "⏰ 自动化与任务", "callback_data": make_callback(HELP_MENU_NS, "automation")},
            ],
            [
                {"text": "🏠 返回主菜单", "callback_data": make_callback(HOME_MENU_NS, "main")},
            ],
        ]
    }


def _build_help_payload(
    section: str = "home",
    *,
    access: dict[str, bool] | None = None,
) -> tuple[str, dict]:
    normalized = str(section or "home").strip().lower()
    flags = dict(DEFAULT_ACCESS)
    flags.update(dict(access or {}))
    if normalized == "chat":
        return (
            "🚀 **对话与多模态**\n\n"
            "• 直接发送文本、图片、语音即可。\n"
            "• 发图片后可继续追问“这是什么”“帮我总结”。\n"
            "• 发视频链接时可直接下载或生成摘要。\n"
            "• 想新开上下文用 `/new`。",
            {
                "actions": [
                    [
                        {"text": "返回帮助", "callback_data": make_callback(HELP_MENU_NS, "home")},
                        {"text": "返回主菜单", "callback_data": make_callback(HOME_MENU_NS, "main")},
                    ]
                ]
            },
        )
    if normalized == "skills":
        lines = [
            "🧩 **技能与工具**\n\n",
            "• `/skills` 浏览已安装技能。\n",
            "• `/daily` 可查天气、时间、汇率、币价。\n",
            "• `/account` 管理账号凭据。",
        ]
        if flags.get("stock"):
            lines.insert(2, "• `/stock` 支持查看和管理自选股。\n")
        if flags.get("rss"):
            lines.insert(2, "• `/rss` 支持订阅和刷新 RSS。\n")
        if flags.get("scheduler"):
            lines.insert(2, "• `/schedule` 支持查看和删除定时任务。\n")
        if flags.get("accounting"):
            lines.insert(2, "• `/acc` 支持快捷记账。\n")
        return (
            "".join(lines),
            {
                "actions": [
                    [
                        {"text": "查看 Skills", "callback_data": make_callback(HOME_MENU_NS, "skills")},
                        {"text": "返回帮助", "callback_data": make_callback(HELP_MENU_NS, "home")},
                    ]
                ]
            },
        )
    if normalized == "ops":
        return (
            "⚙️ **模型与用量**\n\n"
            "• `/model` 查看和切换模型。\n"
            "• `/usage` 查看按模型聚合的 token 用量。\n"
            "• `/usage today` 看当天用量。\n"
            "• `/usage reset` 立即清空统计；菜单按钮会二次确认。",
            {
                "actions": [
                    [
                        {"text": "模型", "callback_data": make_callback(HOME_MENU_NS, "model")},
                        {"text": "用量", "callback_data": make_callback(HOME_MENU_NS, "usage")},
                    ],
                    [
                        {"text": "返回帮助", "callback_data": make_callback(HELP_MENU_NS, "home")},
                    ],
                ]
            },
        )
    if normalized == "automation":
        lines = ["⏰ **自动化与任务**\n\n"]
        if flags.get("heartbeat"):
            lines.append("• `/heartbeat` 管理巡检节奏和 checklist。\n")
        if flags.get("scheduler"):
            lines.append("• `/schedule` 查看和删除定时任务。\n")
        lines.append("• `/task` 查看最近任务和未完成任务。\n")
        lines.append("• `/stop` 可中断当前任务。")
        action_row = [{"text": "任务", "callback_data": make_callback(HOME_MENU_NS, "task")}]
        if flags.get("heartbeat"):
            action_row.insert(
                0,
                {"text": "Heartbeat", "callback_data": make_callback(HOME_MENU_NS, "heartbeat")},
            )
        return (
            "".join(lines),
            {
                "actions": [
                    action_row,
                    [
                        {"text": "返回帮助", "callback_data": make_callback(HELP_MENU_NS, "home")},
                    ],
                ]
            },
        )
    return (
        "ℹ️ **Ikaros 使用指南**\n\n"
        "按类别查看最常用功能和命令：",
        _help_categories_ui(),
    )


async def start(ctx: UnifiedContext) -> None:
    """处理 /start 命令，显示欢迎消息和功能菜单"""
    if not await check_permission_unified(ctx):
        return

    await ctx.reply(WELCOME_MESSAGE, ui=get_main_menu_ui(_access_flags(ctx)))


async def handle_new_command(ctx: UnifiedContext) -> None:
    """处理 /new 命令，清空聊天上下文"""
    if not await check_permission_unified(ctx):
        return

    from user_context import clear_context

    # clear_context currently expects telegram context?
    # Let's check user_context.py later. For now pass ctx.platform_ctx
    clear_context(ctx)

    await ctx.reply(
        "🧹 **已开启新对话**\n\n"
        "之前的短期对话上下文已清空。\n"
        "不用担心，重要的长期记忆仍然保留！🧠"
    )


async def stop_command(ctx: UnifiedContext) -> None:
    """
    处理 /stop 命令，中断当前正在执行的任务。
    这个命令应该在任何时候都能响应。
    """
    logger.info(f"Received stop command from user {ctx.message.user.id}")
    await ctx.reply("🛑 正在尝试停止当前任务...")
    # 权限检查
    if not await check_permission_unified(ctx):
        return

    user_id = ctx.message.user.id

    from core.channel_runtime_store import channel_runtime_store
    from core.task_manager import task_manager
    from core.heartbeat_store import heartbeat_store
    from core.subagent_supervisor import subagent_supervisor

    active_info = task_manager.get_task_info(user_id)
    todo_path = active_info.get("todo_path") if isinstance(active_info, dict) else None
    heartbeat_path = (
        active_info.get("heartbeat_path") if isinstance(active_info, dict) else None
    )
    active_task_id = (
        active_info.get("active_task_id") if isinstance(active_info, dict) else None
    )
    session_snapshot = await session_task_store.get_active(str(user_id))
    if not active_task_id:
        channel_active = channel_runtime_store.get_active_task(
            platform=str(ctx.message.platform or "").strip().lower(),
            platform_user_id=str(user_id),
        )
        if channel_active:
            active_task_id = str(channel_active.get("id") or "")
        hb_active = await heartbeat_store.get_session_active_task(str(user_id))
        if hb_active and not active_task_id:
            active_task_id = str(hb_active.get("id") or "")
            heartbeat_path = str(heartbeat_store.heartbeat_path(str(user_id)))
    if session_snapshot is None and active_task_id:
        session_snapshot = await session_task_store.get(str(active_task_id))

    # 尝试取消任务
    cancelled_desc = await task_manager.cancel_task(user_id)
    subagent_cancel = {"cancelled": 0, "task_ids": []}
    try:
        subagent_cancel = await subagent_supervisor.cancel_for_user(
            user_id=str(user_id),
            reason="cancelled_by_stop_command",
        )
    except Exception as exc:
        logger.warning("stop command subagent cancel failed: %s", exc)

    subagent_cancelled_total = int(subagent_cancel.get("cancelled") or 0)

    if active_task_id:
        channel_runtime_store.update_active_task(
            platform=str(ctx.message.platform or "").strip().lower(),
            platform_user_id=str(user_id),
            status="cancelled",
            needs_confirmation=False,
            confirmation_deadline="",
            clear_active=True,
            result_summary="Cancelled by /stop command.",
        )
        await heartbeat_store.update_session_active_task(
            str(user_id),
            status="cancelled",
            needs_confirmation=False,
            confirmation_deadline="",
            clear_active=True,
            result_summary="Cancelled by /stop command.",
        )
        await heartbeat_store.release_lock(user_id)
        await heartbeat_store.append_session_event(
            str(user_id), f"user_cancelled:{active_task_id}"
        )

    if cancelled_desc or active_task_id or subagent_cancelled_total > 0:
        task_type_text = cancelled_desc or "subagent_background"
        lines = ["🛑 **已中断任务**", ""]
        if session_snapshot is not None:
            lines.extend(
                build_session_brief_lines(
                    session_task_id=session_snapshot.session_task_id
                    or str(active_task_id or "").strip(),
                    stage_index=session_snapshot.stage_index,
                    stage_total=session_snapshot.stage_total,
                    stage_title=session_snapshot.stage_title,
                )
            )
        elif active_task_id:
            lines.append(f"任务：`{str(active_task_id).strip()}`")
        lines.append(f"任务类型：{task_type_text}")
        if subagent_cancelled_total > 0:
            lines.append(
                "🧩 Subagent 任务: "
                f"已取消 {subagent_cancelled_total} 个后台子任务"
            )
        if heartbeat_path:
            lines.append(f"💓 心跳文件：`{heartbeat_path}`")
        if todo_path:
            lines.append(f"📋 旧任务文件：`{todo_path}`")
        lines.extend(["", "如需继续，请重新发送您的请求。"])
        await ctx.reply("\n".join(lines).strip())
    else:
        await ctx.reply(
            "ℹ️ **当前没有正在执行的任务**\n\n您可以直接发送新消息开始对话。"
        )


async def help_command(ctx: UnifiedContext) -> None:
    """处理 /help 命令"""
    if not await check_permission_unified(ctx):
        return

    payload, ui = _build_help_payload("home", access=_access_flags(ctx))
    await ctx.reply(payload, ui=ui)


async def handle_home_callback(ctx: UnifiedContext) -> int:
    if not await check_permission_unified(ctx):
        return CONVERSATION_END

    data = ctx.callback_data
    if not data:
        return CONVERSATION_END

    return await _dispatch_home_callback_data(ctx, data)


async def _dispatch_home_callback_data(ctx: UnifiedContext, data: str) -> int:
    action, parts = parse_callback(data, HOME_MENU_NS)
    if not action:
        action, parts = parse_callback(data, HELP_MENU_NS)
        if action:
            payload, ui = _build_help_payload(action, access=_access_flags(ctx))
            await edit_callback_message(ctx, payload, ui=ui)
            return CONVERSATION_END
        return CONVERSATION_END

    try:
        if action == "main":
            await edit_callback_message(
                ctx,
                WELCOME_MESSAGE,
                ui=get_main_menu_ui(_access_flags(ctx)),
            )
            return CONVERSATION_END
        if action == "help":
            payload, ui = _build_help_payload("home", access=_access_flags(ctx))
            await edit_callback_message(ctx, payload, ui=ui)
            return CONVERSATION_END
        if action == "skills":
            from handlers.skill_handlers import _build_skills_home_payload

            payload, ui = _build_skills_home_payload()
            await edit_callback_message(ctx, payload, ui=ui)
            return CONVERSATION_END
        if action == "model":
            from handlers.model_handlers import _build_summary_payload

            payload, ui = _build_summary_payload()
            await edit_callback_message(ctx, payload, ui=ui)
            return CONVERSATION_END
        if action == "usage":
            from handlers.usage_handlers import _build_usage_payload

            payload, ui = _build_usage_payload("show")
            await edit_callback_message(ctx, payload, ui=ui)
            return CONVERSATION_END
        if action == "heartbeat":
            if not await require_feature_access(ctx, "heartbeat"):
                return CONVERSATION_END
            from handlers.heartbeat_handlers import _build_heartbeat_payload

            payload, ui = await _build_heartbeat_payload(get_effective_user_id(ctx))
            await edit_callback_message(ctx, payload, ui=ui)
            return CONVERSATION_END
        if action == "task":
            from handlers.task_handlers import _build_task_list_payload

            payload, ui = await _build_task_list_payload(ctx, view="recent")
            await edit_callback_message(ctx, payload, ui=ui)
            return CONVERSATION_END
        if action == "accounting":
            if not await require_feature_access(ctx, "accounting"):
                return CONVERSATION_END
            from extension.skills.registry import skill_registry as skill_loader

            module = skill_loader.import_skill_module("quick_accounting")
            builder = getattr(module, "build_accounting_info_payload", None)
            if not callable(builder):
                await edit_callback_message(
                    ctx,
                    "❌ 记账技能当前不可用，请稍后重试。",
                    ui=get_main_menu_ui(_access_flags(ctx)),
                )
                return CONVERSATION_END

            payload, ui = await builder(ctx)
            await edit_callback_message(ctx, payload, ui=ui)
            return CONVERSATION_END
        if action == "chatlog":
            await edit_callback_message(
                ctx,
                "🔎 对话检索\n\n直接发送 `/chatlog <关键词>`，例如：`/chatlog PR`。",
                ui={
                    "actions": [
                        [
                            {"text": "示例：PR", "callback_data": make_callback("chatlog", "hint", "PR")},
                            {"text": "返回主菜单", "callback_data": make_callback(HOME_MENU_NS, "main")},
                        ]
                    ]
                },
            )
            return CONVERSATION_END
        if action == "compact":
            from user_context import get_context_length

            try:
                dialog_count = await get_context_length(ctx, get_effective_user_id(ctx))
            except Exception:
                dialog_count = 0
            await edit_callback_message(
                ctx,
                "🗜️ 会话压缩\n\n"
                f"当前上下文消息数：`{dialog_count}`\n\n"
                "确认后会把更早历史压成摘要，保留最近原始消息。",
                ui={
                    "actions": [
                        [
                            {"text": "确认压缩", "callback_data": make_callback("compact", "run")},
                            {"text": "返回主菜单", "callback_data": make_callback(HOME_MENU_NS, "main")},
                        ]
                    ]
                },
            )
            return CONVERSATION_END
    except Exception as exc:
        logger.error("Error in handle_home_callback: %s", exc, exc_info=True)
        await ctx.reply("❌ 操作失败，请重试或输入 /start 重启。")
        return CONVERSATION_END

    return CONVERSATION_END


async def button_callback(ctx: UnifiedContext) -> int:
    """处理通用内联键盘按钮点击（非会话入口）"""
    if not await check_permission_unified(ctx):
        return CONVERSATION_END

    data = ctx.callback_data
    if not data:
        return CONVERSATION_END

    # Answer callback to stop spinner
    await ctx.answer_callback()

    msg_id = ctx.message.id

    try:
        if data in {"task_continue", "task_stop"}:
            from core.channel_runtime_store import channel_runtime_store
            from core.heartbeat_store import heartbeat_store
            from manager.relay.closure_service import manager_closure_service

            hb_user_id = str(ctx.callback_user_id or ctx.message.user.id)
            active_task = channel_runtime_store.get_active_task(
                platform=str(ctx.message.platform or "").strip().lower(),
                platform_user_id=hb_user_id,
            )
            if not active_task:
                active_task = await heartbeat_store.get_session_active_task(hb_user_id)
            if not active_task or active_task.get("status") != "waiting_user":
                await ctx.reply("ℹ️ 当前没有等待确认的任务。")
                return CONVERSATION_END

            task_id = str(active_task.get("id"))
            if data == "task_continue":
                resume = await manager_closure_service.resume_waiting_task(
                    user_id=hb_user_id,
                    user_message="",
                    source="button",
                )
                if bool(resume.get("ok")):
                    await heartbeat_store.append_session_event(
                        hb_user_id, f"user_confirm_continue:{task_id}"
                    )
                    await ctx.reply(str(resume.get("message") or "✅ 已确认继续执行。"))
                else:
                    await ctx.reply(
                        str(
                            resume.get("message")
                            or "⚠️ 当前任务暂时无法继续，请稍后重试。"
                        )
                    )
            else:
                channel_runtime_store.update_active_task(
                    platform=str(ctx.message.platform or "").strip().lower(),
                    platform_user_id=hb_user_id,
                    status="cancelled",
                    needs_confirmation=False,
                    confirmation_deadline="",
                    clear_active=True,
                    result_summary="Cancelled during confirmation stage.",
                )
                await heartbeat_store.update_session_active_task(
                    hb_user_id,
                    status="cancelled",
                    needs_confirmation=False,
                    confirmation_deadline="",
                    clear_active=True,
                    result_summary="Cancelled during confirmation stage.",
                )
                await heartbeat_store.release_lock(hb_user_id)
                await heartbeat_store.append_session_event(
                        hb_user_id, f"user_confirm_stop:{task_id}"
                )
                await ctx.reply("🛑 已停止该任务。")
            return CONVERSATION_END
        legacy_map = {
            "help": make_callback(HOME_MENU_NS, "help"),
            "back_to_main": make_callback(HOME_MENU_NS, "main"),
            "ai_chat": make_callback(HELP_MENU_NS, "chat"),
            "settings": make_callback(HOME_MENU_NS, "model"),
            "watchlist": make_callback(HOME_MENU_NS, "skills"),
            "list_subs": make_callback(HOME_MENU_NS, "skills"),
            "remind_help": make_callback(HELP_MENU_NS, "automation"),
        }
        remapped = legacy_map.get(data)
        if remapped:
            return await _dispatch_home_callback_data(ctx, remapped)

    except Exception as e:
        logger.error(f"Error in button_callback for data {data}: {e}")
        # 尝试通知用户发生错误，如果 edit 失败
        try:
            await ctx.reply("❌ 操作失败，请重试或输入 /start 重启。")
        except Exception:
            pass

    return CONVERSATION_END
