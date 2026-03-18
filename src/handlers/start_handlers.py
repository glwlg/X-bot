import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from core.platform.models import UnifiedContext
from core.session_task_store import session_task_store
from core.task_cards import build_session_brief_lines
from .base_handlers import check_permission_unified, CONVERSATION_END

logger = logging.getLogger(__name__)

WELCOME_MESSAGE = (
    "👋 **欢迎使用 X-Bot！**\n\n"
    "我是您的全能 AI 助手，支持 **自然语言交互** 与 **多模态分析**。\n\n"
    "💬 **直接对话**：你可以像朋友一样跟我聊天。\n"
    "🛠️ **执行任务**：下载视频、监控股票、阅读PDF、生成播客等。\n"
    "🧬 **自我进化**：遇到不会的问题，我会尝试自己写代码解决！\n\n"
    "👇 点击下方 **[ℹ️ 帮助]** 查看所有指令与技能。"
)


def get_main_menu_keyboard():
    return [
        [
            InlineKeyboardButton("ℹ️ 使用帮助 / Help", callback_data="help"),
        ],
    ]


async def start(ctx: UnifiedContext) -> None:
    """处理 /start 命令，显示欢迎消息和功能菜单"""
    if not await check_permission_unified(ctx):
        return

    reply_markup = InlineKeyboardMarkup(get_main_menu_keyboard())

    await ctx.reply(
        WELCOME_MESSAGE,
        reply_markup=reply_markup,
    )


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
        hb_active = await heartbeat_store.get_session_active_task(str(user_id))
        if hb_active:
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

    await ctx.reply(
        "ℹ️ **X-Bot 使用指南**\n\n"
        "🚀 **多模态 AI**\n"
        "• **对话**：直接发送文本、语音。\n"
        '• **识图**：发送照片，问 "这是什么"。\n'
        '• **绘图**："画一只赛博朋克风格的猫"。\n'
        "• **翻译**：直接发送需要翻译的内容即可。\n\n"
        "📓 **NotebookLM 知识库**\n"
        '• **播客**："下载这个视频的播客" 或 "生成播客"。\n'
        '• **问答**："询问 Kubernetes 调度原理"。\n'
        '• **管理**：使用 "NotebookLM" 或 "list notebooks"。\n\n'
        "📹 **媒体下载**\n"
        "• 直接发送链接 (YouTube/X/B站等)，支持自动去重。\n"
        '• "下载这个视频的音频 https://..."\n\n'
        "📈 **行情与资讯**\n"
        '• "帮我关注英伟达股票"\n'
        '• "订阅 RSS https://..."\n\n'
        "⏰ **实用工具**\n"
        '• "10分钟后提醒我喝水"\n'
        '• "部署这个仓库 https://..."\n'
        '• "列出运行的服务"\n\n'
        "💡 **技能扩展 (自进化)**\n"
        '• **无师自通**：直接问我 "查询最新 GitHub 趋势"，我会自动学习新技能。\n'
        "• **手动教学**：/teach - 强制触发学习模式\n"
        "• /skills - 查看已安装技能\n\n"
        "**常用命令：**\n"
        "/start 主菜单 | /new 新对话 | /compact 压缩 | /chatlog 检索 | /heartbeat 心跳 | /task 任务"
    )


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
            from core.heartbeat_store import heartbeat_store
            from manager.relay.closure_service import manager_closure_service

            hb_user_id = str(ctx.callback_user_id or ctx.message.user.id)
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

        if data == "ai_chat":
            keyboard = [
                [InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await ctx.edit_message(
                msg_id,
                "💬 **AI 对话模式**\n\n"
                "现在您可以直接发送任何消息，我会用 AI 智能回复！\n\n"
                "💡 提示：直接在对话框输入消息即可，无需点击按钮。",
                reply_markup=reply_markup,
            )
            return CONVERSATION_END

        elif data == "help":
            keyboard = [
                [InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await ctx.edit_message(
                msg_id,
                "ℹ️ **X-Bot 使用指南**\n\n"
                "🚀 **多模态 AI**\n"
                "• **对话**：直接发送文本、语音。\n"
                '• **识图**：发送照片，问 "这是什么"。\n'
                '• **绘图**："画一只赛博朋克风格的猫"。\n'
                "• **翻译**：直接发送需要翻译的内容即可。\n\n"
                "📓 **NotebookLM 知识库**\n"
                '• **播客**："下载这个视频的播客" 或 "生成播客"。\n'
                '• **问答**："询问 Kubernetes 调度原理"。\n'
                '• **管理**：使用 "NotebookLM" 或 "list notebooks"。\n\n'
                "📹 **媒体下载**\n"
                "• 直接发送链接 (YouTube/X/B站等)，支持自动去重。\n"
                '• "下载这个视频的音频 https://..."\n\n'
                "📈 **行情与资讯**\n"
                '• "帮我关注英伟达股票"\n'
                '• "订阅 RSS https://..."\n\n'
                "⏰ **实用工具**\n"
                '• "10分钟后提醒我喝水"\n'
                '• "部署这个仓库 https://..."\n'
                '• "列出运行的服务"\n\n'
                "💡 **技能扩展**\n"
                "• /teach - 教我学会新技能 (自定义代码)\n"
                "• /skills - 查看已安装技能\n\n"
                "**常用命令：**\n"
                "/start 主菜单 | /new 新对话 | /chatlog 检索 | /heartbeat 心跳 | /task 任务",
                reply_markup=reply_markup,
            )
            return CONVERSATION_END

        elif data == "settings":
            keyboard = [
                [InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            from core.model_config import get_current_model

            openai_model = get_current_model()

            await ctx.edit_message(
                msg_id,
                "⚙️ **设置**\n\n"
                "当前配置：\n"
                f"• 对话模型：{openai_model}\n"
                "• 视频质量：最高\n"
                "• 文件大小限制：49 MB\n\n"
                "更多设置功能即将推出...",
                reply_markup=reply_markup,
            )
            return CONVERSATION_END

        elif data == "platforms":
            keyboard = [
                [InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await ctx.edit_message(
                msg_id,
                "📊 **支持的视频平台**\n\n"
                "✅ X (Twitter) - twitter.com, x.com\n"
                "✅ YouTube - youtube.com, youtu.be\n"
                "✅ Instagram - instagram.com\n"
                "✅ TikTok - tiktok.com\n"
                "✅ Bilibili - bilibili.com\n\n"
                "支持绝大多数公开视频链接！",
                reply_markup=reply_markup,
            )
            return CONVERSATION_END

        elif data == "watchlist":
            keyboard = [
                [InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            user_id = ctx.message.user.id
            from core.state_store import get_user_watchlist
            from services.stock_service import fetch_stock_quotes, format_stock_message

            watchlist = await get_user_watchlist(user_id)

            if not watchlist:
                text = (
                    "📈 **我的自选股**\n\n"
                    "您还没有添加自选股。\n\n"
                    "**使用方法：**\n"
                    "• 发送「帮我关注仙鹤股份」添加\n"
                    "• 支持多只：「关注红太阳和联环药业」\n"
                    "• /stock list 查看列表"
                )
            else:
                stock_codes = [item["stock_code"] for item in watchlist]
                quotes = await fetch_stock_quotes(stock_codes)

                if quotes:
                    text = format_stock_message(quotes)
                else:
                    lines = ["📈 **我的自选股**\n"]
                    for item in watchlist:
                        lines.append(f"• {item['stock_name']} ({item['stock_code']})")
                    text = "\n".join(lines)

                text += "\n\n发送「取消关注 XX」可删除"

            await ctx.edit_message(msg_id, text, reply_markup=reply_markup)
            return CONVERSATION_END

        elif data == "list_subs":
            keyboard = [
                [InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            user_id = ctx.message.user.id
            from core.state_store import get_user_subscriptions

            subs = await get_user_subscriptions(user_id)

            if not subs:
                text = (
                    "📢 **我的订阅**\n\n"
                    "您还没有订阅任何内容。\n\n"
                    "**使用方法：**\n"
                    "• /rss add `<URL>` : 订阅 RSS\n"
                )
            else:
                text = "📢 **我的订阅列表**\n\n"
                for sub in subs:
                    title = sub["title"] or "无标题"
                    text += f"• `#{sub['id']}` [RSS] [{title}]({sub['feed_url']})\n"

                text += "\n使用 /rss remove `<订阅ID>` 取消订阅。"

            await ctx.edit_message(msg_id, text, reply_markup=reply_markup)
            return CONVERSATION_END

        elif data == "remind_help":
            keyboard = [
                [InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await ctx.edit_message(
                msg_id,
                "⏰ **定时提醒使用帮助**\n\n"
                "请直接发送二级命令设置提醒：\n\n"
                "• **/remind 10m 关火** (10分钟后)\n"
                "• **/remind 1h30m 休息一下** (1小时30分后)\n\n"
                "• **/remind help** 查看说明\n\n"
                "时间单位支持：s(秒), m(分), h(时), d(天)",
                reply_markup=reply_markup,
            )
            return CONVERSATION_END

        elif data == "back_to_main":
            # 重新显示主菜单
            reply_markup = InlineKeyboardMarkup(get_main_menu_keyboard())
            await ctx.edit_message(
                msg_id,
                WELCOME_MESSAGE,
                reply_markup=reply_markup,
            )
            return CONVERSATION_END

    except Exception as e:
        logger.error(f"Error in button_callback for data {data}: {e}")
        # 尝试通知用户发生错误，如果 edit 失败
        try:
            await ctx.reply("❌ 操作失败，请重试或输入 /start 重启。")
        except Exception:
            pass

    return CONVERSATION_END
