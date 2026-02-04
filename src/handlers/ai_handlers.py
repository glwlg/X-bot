import time
import logging
import base64
from core.platform.models import UnifiedContext, MessageType
import random

from core.config import gemini_client, GEMINI_MODEL

from user_context import get_user_context, add_message
from repositories import get_user_settings
from stats import increment_stat

logger = logging.getLogger(__name__)

# 思考提示消息
THINKING_MESSAGE = "🤔 让我想想..."


async def handle_ai_chat(ctx: UnifiedContext) -> None:
    """
    处理普通文本消息，使用 Gemini AI 生成回复
    支持引用（回复）包含图片或视频的消息
    """
    user_message = ctx.message.text
    # Legacy fallbacks
    update = ctx.platform_event
    context = ctx.platform_ctx

    chat_id = ctx.message.chat.id
    user_id = ctx.message.user.id

    if not user_message:
        return

    # 0. Save user message immediately to ensure persistence even if we return early
    # Note: We save the raw user message here.
    # If using history later, we might want to avoid saving duplicates if we constructed a complex prmopt.
    # But for "chat record", raw input is best.
    await add_message(ctx, user_id, "user", user_message)

    # 检查用户权限
    from core.config import is_user_allowed

    if not await is_user_allowed(user_id):
        await ctx.reply(
            f"⛔ 抱歉，您没有使用 AI 对话功能的权限。\n您的 ID 是: `{user_id}`\n\n"
        )
        return

    # 0.5 Fast-track: Detected video URL -> Show Options (Download vs Summarize)
    from utils import extract_video_url

    video_url = extract_video_url(user_message)
    if video_url:
        logger.info(f"Detected video URL: {video_url}, presenting options")

        # Save URL to context for callback access
        if context:
            ctx.user_data["pending_video_url"] = video_url
            logger.info(f"[AIHandler] Set pending_video_url for {user_id}: {video_url}")

        # Create Inline Keyboard with options
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = [
            [
                InlineKeyboardButton(
                    "📹 下载视频", callback_data="action_download_video"
                ),
                InlineKeyboardButton(
                    "📝 生成摘要", callback_data="action_summarize_video"
                ),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await ctx.reply(
            f"🔗 **已识别视频链接**\n\n您可以选择以下操作：", reply_markup=reply_markup
        )
        return

    # 检查是否开启了沉浸式翻译
    settings = await get_user_settings(user_id)
    if settings.get("auto_translate", 0):
        # 检查是否是退出指令
        if user_message.strip().lower() in [
            "/cancel",
            "退出",
            "关闭翻译",
            "退出翻译",
            "cancel",
        ]:
            from repositories import set_translation_mode

            await set_translation_mode(user_id, False)
            await ctx.reply("🚫 已退出沉浸式翻译模式。")
            return

        # 翻译模式开启
        thinking_msg = await ctx.reply("🌍 翻译中...")
        await ctx.send_chat_action(action="typing")

        try:
            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=user_message,
                config={
                    "system_instruction": (
                        "你是一个专业的翻译助手。请根据以下规则进行翻译：\n"
                        "1. 如果输入是中文，请翻译成英文。\n"
                        "2. 如果输入是其他语言，请翻译成简体中文。\n"
                        "3. 只输出译文，不要包含任何解释或额外的文本。\n"
                        "4. 保持原文的语气和格式。"
                    ),
                },
            )
            if response.text:
                translation_text = f"🌍 **译文**\n\n{response.text}"
                msg_id = getattr(
                    thinking_msg, "message_id", getattr(thinking_msg, "id", None)
                )
                await ctx.edit_message(msg_id, translation_text)
                await add_message(ctx, user_id, "model", translation_text)
                # 统计
                await increment_stat(user_id, "translations_count")
            else:
                msg_id = getattr(
                    thinking_msg, "message_id", getattr(thinking_msg, "id", None)
                )
                await ctx.edit_message(msg_id, "❌ 无法翻译。")
        except Exception as e:
            logger.error(f"Translation error: {e}")
            msg_id = getattr(
                thinking_msg, "message_id", getattr(thinking_msg, "id", None)
            )
            await ctx.edit_message(msg_id, "❌ 翻译服务出错。")
        return

    # --- Agent Orchestration ---
    from core.agent_orchestrator import agent_orchestrator

    # 1. 检查是否引用了消息 (Reply Context)
    from .message_utils import process_reply_message, process_and_send_code_files

    extra_context = ""
    has_media, reply_extra_context, media_data, mime_type = await process_reply_message(
        ctx
    )

    if reply_extra_context:
        extra_context += reply_extra_context

    # Check if we should abort (e.g. file too big)
    if ctx.message.reply_to_message:
        r = ctx.message.reply_to_message
        is_media = r.type in [MessageType.VIDEO, MessageType.AUDIO, MessageType.VOICE]
        if is_media and not has_media:
            return

    # URL 逻辑已移交给 Agent (skill: web_browser, download_video)
    # 不再进行硬编码预加载或弹窗

    # 随机选择一种"消息已收到"的提示
    RECEIVED_PHRASES = [
        "📨 收到！大脑急速运转中...",
        "⚡ 信号已接收，开始解析...",
        "🍪 Bip Bip! 消息直达核心...",
        "📡 神经连接建立中...",
        "💭 正在调取相关记忆...",
        "🐌 稍微有点堵车，马上就好...",
        "✨ 指令已确认，准备施法...",
    ]

    if not has_media:
        thinking_msg = await ctx.reply(random.choice(RECEIVED_PHRASES))
    else:
        thinking_msg = await ctx.reply("🤔 让我看看引用具体内容...")

    # 3. 构建消息上下文 (History)
    final_user_message = user_message
    if extra_context:
        final_user_message = extra_context + "用户请求：" + user_message

    # User message already saved at start of function.
    # await add_message(context, user_id, "user", final_user_message)

    # 发送"正在输入"状态
    # 发送"正在输入"状态
    await ctx.send_chat_action(action="typing")

    import asyncio

    # 动态加载词库
    LOADING_PHRASES = [
        "🤖 调用赛博算力中...",
        "💭 此问题稍显深奥...",
        "🛁 顺手清洗下数据管道...",
        "📡 正在尝试连接火星通讯...",
        "🍪 先给 AI 喂块饼干补充体力...",
        "🐌 稍等，前面有点堵...",
        "📚 翻阅百科全书中...",
        "🔨 正在狂敲代码实现需求...",
        "🌌 试图穿越虫洞寻找答案...",
        "🧹 清理一下内存碎片...",
        "🔌 检查下网线接好没...",
        "🎨 正在为您绘制思维导图...",
        "🍕 吃口披萨，马上回来...",
        "🧘 数字冥想中...",
        "🏃 全力冲刺中...",
    ]

    # 共享状态
    state = {"last_update_time": time.time(), "final_text": "", "running": True}

    async def loading_animation():
        """
        后台动画任务：每隔几秒检查是否有新内容。
        如果卡住了（比如在调用 Tools），通过修改消息来“卖萌”。
        """
        while state["running"]:
            await asyncio.sleep(4)  # Check every 4s
            if not state["running"]:
                break

            now = time.time()
            # 如果超过 5 秒没有更新文本（说明卡在 Tool 或者生成慢）
            if now - state["last_update_time"] > 5:
                phrase = random.choice(LOADING_PHRASES)

                # 如果已经有一部分文本了，附在后面；如果是空的，直接显示
                display_text = state["final_text"]
                if display_text:
                    display_text += f"\n\n⏳ {phrase}"
                else:
                    display_text = phrase

                try:
                    msg_id = getattr(
                        thinking_msg, "message_id", getattr(thinking_msg, "id", None)
                    )
                    await ctx.edit_message(msg_id, display_text)
                except Exception as e:
                    logger.debug(f"Animation edit failed: {e}")

                # Update time to avoid spamming edits (waiting another cycle)
                state["last_update_time"] = time.time()

    # Default to True for backward compatibility or if adapter missing
    can_update = getattr(ctx._adapter, "can_update_message", True)

    # 启动动画任务 (仅当支持消息更新时，也就是非 DingTalk)
    animation_task = None
    if can_update:
        animation_task = asyncio.create_task(loading_animation())

    try:
        message_history = []

        # 构建当前消息
        current_msg_parts = []
        current_msg_parts.append({"text": final_user_message})

        if has_media and media_data:
            current_msg_parts.append(
                {
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": base64.b64encode(bytes(media_data)).decode("utf-8"),
                    }
                }
            )

        # 获取历史上下文
        # HACK: Because 'add_message' only saves TEXT to DB, we lose the media info if we just fetch from DB.
        # So we need to:
        # 1. Fetch history from DB (which now includes the latest text-only message)
        # 2. POP the last message from history (which is our text-only version)
        # 3. Append our rich 'current_msg_parts' (with Text + Media)

        history = await get_user_context(ctx, user_id)  # Returns list of dicts

        if history and len(history) > 0 and history[-1]["role"] == "user":
            # Check if the last DB message matches our current text (sanity check)
            last_db_text = history[-1]["parts"][0]["text"]
            if last_db_text == final_user_message:
                # Remove it, so we can replace it with the Rich version
                history.pop()

        # 拼接: History + Current Rich Message
        message_history.extend(history)
        message_history.append({"role": "user", "parts": current_msg_parts})

        # B. 调用 Agent Orchestrator
        final_text_response = ""
        last_stream_update = 0

        async for chunk_text in agent_orchestrator.handle_message(ctx, message_history):
            final_text_response += chunk_text
            state["final_text"] = final_text_response
            state["last_update_time"] = time.time()

            # Update UI (Standard Stream) - ONLY if supported
            if can_update:
                now = time.time()
                if now - last_stream_update > 1.0:  # Reduce frequency slightly
                    msg_id = getattr(
                        thinking_msg, "message_id", getattr(thinking_msg, "id", None)
                    )
                    await ctx.edit_message(msg_id, final_text_response)
                    last_stream_update = now

        # 停止动画
        state["running"] = False
        if animation_task:
            animation_task.cancel()  # Ensure it stops immediately

        # 5. 发送最终回复并入库
        if final_text_response:
            # 用户体验优化：为了避免工具产生的中间消息导致最终结果被顶上去需要翻页，
            # 这里改为发送一条新消息作为最终结果，并删除原本的"思考中"消息。

            # 1. 检查是否有 Skill 返回的 UI 组件/按钮
            reply_markup = None
            pending_ui = ctx.user_data.pop("pending_ui", None)
            if pending_ui:
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup

                keyboard = []
                for ui_block in pending_ui:
                    if "actions" in ui_block:
                        # actions should be list of lists (rows)
                        for row in ui_block["actions"]:
                            current_row = []
                            for btn in row:
                                # Start with supporting dict (JSON) format
                                if isinstance(btn, dict):
                                    current_row.append(
                                        InlineKeyboardButton(
                                            text=btn["text"],
                                            callback_data=btn.get("callback_data"),
                                            url=btn.get("url"),
                                        )
                                    )
                                else:
                                    # Fallback for raw objects if mixed
                                    current_row.append(btn)
                            keyboard.append(current_row)

                if keyboard:
                    reply_markup = InlineKeyboardMarkup(keyboard)

            # 2. 发送新消息
            sent_msg = await ctx.reply(final_text_response, reply_markup=reply_markup)

            # 2. 尝试删除旧的思考消息 (如果发送成功)
            # 如果支持编辑（Telegram/Discord），尝试删除思考中消息
            # 如果不支持（DingTalk），思考中消息可能会留着，或者尝试删除（返回 False）
            if sent_msg and can_update:
                try:
                    await thinking_msg.delete()
                except Exception as del_e:
                    logger.warning(f"Failed to delete thinking_msg: {del_e}")
            elif not sent_msg and can_update:  # Fallback edit
                # 如果发送失败（极少见），则降级为编辑旧消息
                msg_id = getattr(
                    thinking_msg, "message_id", getattr(thinking_msg, "id", None)
                )
                sent_msg = await ctx.edit_message(msg_id, final_text_response)

            # 记录模型回复到上下文 (Explicitly save final response)
            await add_message(ctx, user_id, "model", final_text_response)

            # Try to extract code blocks
            final_display_text = await process_and_send_code_files(
                ctx, final_text_response
            )

            if sent_msg and final_display_text != final_text_response and can_update:
                # Only update again if supported
                msg_id = getattr(sent_msg, "message_id", getattr(sent_msg, "id", None))
                await ctx.edit_message(msg_id, final_display_text)

            # 记录统计
            await increment_stat(user_id, "ai_chats")
    except Exception as e:
        state["running"] = False
        if animation_task:
            animation_task.cancel()
        logger.error(f"Agent error: {e}", exc_info=True)

        if str(e) == "Message is not modified":
            pass
        else:
            msg_id = getattr(
                thinking_msg, "message_id", getattr(thinking_msg, "id", None)
            )
            await ctx.edit_message(
                msg_id, f"❌ Agent 运行出错：{e}\n\n请尝试 /new 重置对话。"
            )


async def handle_ai_photo(ctx: UnifiedContext) -> None:
    """
    处理图片消息，使用 Gemini AI 分析图片
    """
    chat_id = ctx.message.chat.id
    user_id = ctx.message.user.id

    # Legacy fallback
    update = ctx.platform_event
    context = ctx.platform_ctx

    # 检查用户权限
    from core.config import is_user_allowed

    if not await is_user_allowed(user_id):
        await ctx.reply(f"⛔ 抱歉，您没有使用 AI 功能的权限。\n您的 ID 是: `{user_id}`")
        return

    # 获取图片（选择最大分辨率）
    # Use fallback to access raw photo object for now
    if not update.message.photo:
        return
    photo = update.message.photo[-1]
    caption = ctx.message.caption or "请描述这张图片"

    # Save to history immediately
    await add_message(ctx, user_id, "user", f"【用户发送了一张图片】 {caption}")

    # 立即发送"正在分析"提示
    thinking_msg = await ctx.reply("🔍 让我仔细看看这张图...")

    # 发送"正在输入"状态
    await ctx.send_chat_action(action="typing")

    try:
        # 下载图片
        image_bytes = await ctx.download_file(photo.file_id)

        # 构建带图片的内容
        contents = [
            {
                "parts": [
                    {"text": caption},
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": base64.b64encode(bytes(image_bytes)).decode(
                                "utf-8"
                            ),
                        }
                    },
                ]
            }
        ]

        # 调用 Gemini API
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config={
                "system_instruction": "你是一个友好的助手，可以分析图片并回答问题。请用中文回复。",
            },
        )

        if response.text:
            # Try to extract code blocks, send files, and get cleaned text
            from .message_utils import process_and_send_code_files

            display_text = await process_and_send_code_files(ctx, response.text)

            # 更新消息
            # 更新消息
            msg_id = getattr(
                thinking_msg, "message_id", getattr(thinking_msg, "id", None)
            )
            await ctx.edit_message(msg_id, display_text)

            # Save model response to history
            await add_message(ctx, user_id, "model", response.text)

            # 记录统计
            await increment_stat(user_id, "photo_analyses")

        else:
            msg_id = getattr(
                thinking_msg, "message_id", getattr(thinking_msg, "id", None)
            )
            await ctx.edit_message(msg_id, "抱歉，我无法分析这张图片。请稍后再试。")

    except Exception as e:
        logger.error(f"AI photo analysis error: {e}")
        msg_id = getattr(thinking_msg, "message_id", getattr(thinking_msg, "id", None))
        await ctx.edit_message(msg_id, "❌ 图片分析失败，请稍后再试。")


async def handle_ai_video(ctx: UnifiedContext) -> None:
    """
    处理视频消息，使用 Gemini AI 分析视频
    """
    chat_id = ctx.message.chat.id
    user_id = ctx.message.user.id

    # Legacy fallback
    update = ctx.platform_event
    context = ctx.platform_ctx

    # 检查用户权限
    from core.config import is_user_allowed

    if not await is_user_allowed(user_id):
        await ctx.reply(f"⛔ 抱歉，您没有使用 AI 功能的权限。\n您的 ID 是: `{user_id}`")
        return

    # 获取视频
    video = update.message.video
    if not video:
        return

    caption = ctx.message.caption or "请分析这个视频的内容"

    # Save to history immediately
    await add_message(ctx, user_id, "user", f"【用户发送了一个视频】 {caption}")

    # 检查视频大小（Gemini 有限制）
    # 检查视频大小（Gemini 有限制）
    # 检查视频大小（Gemini 有限制）
    if video.file_size and video.file_size > 20 * 1024 * 1024:  # 20MB 限制
        await ctx.reply(
            "⚠️ 视频文件过大（超过 20MB），无法分析。\n\n请尝试发送较短的视频片段。"
        )
        return

    # 立即发送"正在分析"提示
    thinking_msg = await ctx.reply("🎬 视频分析中，请稍候片刻...")

    # 发送"正在输入"状态
    await ctx.send_chat_action(action="typing")

    try:
        # 下载视频
        video_bytes = await ctx.download_file(video.file_id)

        # 获取 MIME 类型
        mime_type = video.mime_type or "video/mp4"

        # 构建带视频的内容
        contents = [
            {
                "parts": [
                    {"text": caption},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": base64.b64encode(bytes(video_bytes)).decode(
                                "utf-8"
                            ),
                        }
                    },
                ]
            }
        ]

        # 调用 Gemini API
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config={
                "system_instruction": "你是一个友好的助手，可以分析视频内容并回答问题。请用中文回复。",
            },
        )

        if response.text:
            # Try to extract code blocks, send files, and get cleaned text
            from .message_utils import process_and_send_code_files

            display_text = await process_and_send_code_files(ctx, response.text)

            # Update the thinking message with the cleaned text
            msg_id = getattr(
                thinking_msg, "message_id", getattr(thinking_msg, "id", None)
            )
            await ctx.edit_message(msg_id, display_text)

            # Save model response to history
            await add_message(ctx, user_id, "model", response.text)

            # 记录统计
            await increment_stat(user_id, "video_analyses")
        else:
            msg_id = getattr(
                thinking_msg, "message_id", getattr(thinking_msg, "id", None)
            )
            await ctx.edit_message(msg_id, "抱歉，我无法分析这个视频。请稍后再试。")

    except Exception as e:
        logger.error(f"AI video analysis error: {e}")
        msg_id = getattr(thinking_msg, "message_id", getattr(thinking_msg, "id", None))
        await ctx.edit_message(
            msg_id,
            "❌ 视频分析失败，请稍后再试。\n\n"
            "可能的原因：\n"
            "• 视频格式不支持\n"
            "• 视频时长过长\n"
            "• 服务暂时不可用",
        )


async def handle_sticker_message(ctx: UnifiedContext) -> None:
    """
    处理表情包消息，将其转换为图片进行分析
    """
    user_id = ctx.message.user.id
    update = ctx.platform_event

    # 检查用户权限
    from core.config import is_user_allowed

    if not await is_user_allowed(user_id):
        return  # Silent ignore for stickers if unauthorized? Or reply?

    sticker = update.message.sticker
    if not sticker:
        return

    # Check if animated or video sticker (might be harder to handle)
    is_animated = getattr(sticker, "is_animated", False)
    is_video = getattr(sticker, "is_video", False)

    caption = "请描述这个表情包的情感和内容"

    # Save to history
    await add_message(ctx, user_id, "user", f"【用户发送了一个表情包】")

    thinking_msg = await ctx.reply("🤔 这个表情包有点意思...")
    await ctx.send_chat_action(action="typing")

    try:
        # Download
        file_bytes = await ctx.download_file(sticker.file_id)

        mime_type = "image/webp"
        if is_animated:
            # TGS format (lottie). API might not support it directly as image.
            # Maybe treat as document? Or skip?
            # Start with supporting static webp and video webm
            pass
        if is_video:
            mime_type = "video/webm"

        # 构建内容
        contents = [
            {
                "parts": [
                    {"text": caption},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": base64.b64encode(bytes(file_bytes)).decode("utf-8"),
                        }
                    },
                ]
            }
        ]

        # Call API
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config={
                "system_instruction": "你是一个幽默的助手，请分析这个表情包的内容和情感。请用简短有趣的中文回复。",
            },
        )

        if response.text:
            msg_id = getattr(
                thinking_msg, "message_id", getattr(thinking_msg, "id", None)
            )
            await ctx.edit_message(msg_id, response.text)
            await add_message(ctx, user_id, "model", response.text)
            await increment_stat(user_id, "photo_analyses")  # Count as photo
        else:
            msg_id = getattr(
                thinking_msg, "message_id", getattr(thinking_msg, "id", None)
            )
            await ctx.edit_message(msg_id, "😵 没看懂这个表情包...")

    except Exception as e:
        logger.error(f"Sticker analysis error: {e}")
        msg_id = getattr(thinking_msg, "message_id", getattr(thinking_msg, "id", None))
        await ctx.edit_message(msg_id, "❌ 表情包分析失败")
