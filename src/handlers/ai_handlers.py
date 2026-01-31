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
THINKING_MESSAGE = "🤔 正在思考中..."


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
            f"⛔ 抱歉，您没有使用 AI 对话功能的权限。\n"
            f"您的 ID 是: `{user_id}`\n\n"
            "如需下载视频，请使用 /download 命令。"
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
        "📨 收到！大脑正在飞速运转...",
        "⚡ 信号接收完毕，正在解析...",
        "🍪 Bip Bip! 消息已送达核心...",
        "📡 正在建立神经连接...",
        "💭 正在调取相关记忆...",
        "🐌 这里有点堵车，马上就好...",
        "✨ 收到指令，正在施法...",
    ]

    if not has_media:
        thinking_msg = await ctx.reply(random.choice(RECEIVED_PHRASES))
    else:
        thinking_msg = await ctx.reply("🤔 正在分析引用内容...")

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
        "🤖 正在调用赛博算力...",
        "💭 让我好好想一想...",
        "🛁 正在清洗数据管道...",
        "📡 正在连接火星通讯...",
        "🍪 正在给 AI 喂饼干...",
        "🐌 这里有点堵车，稍等...",
        "📚 正在翻阅百科全书...",
        "🔨 正在敲代码实现你的需求...",
        "🌌 正在穿越虫洞寻找答案...",
        "🧹 正在打扫内存碎片...",
        "🔌 正在检查网线有没有松...",
        "🎨 正在绘制思维导图...",
        "🍕 正在吃口披萨补充能量...",
        "🧘 正在进行数字冥想...",
        "🏃 正在全力冲刺...",
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

    # 启动动画任务
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
        history = await get_user_context(ctx, user_id)  # Returns list of dicts

        # 拼接: History + Current
        message_history.extend(history)
        message_history.append({"role": "user", "parts": current_msg_parts})

        # B. 调用 Agent Orchestrator
        final_text_response = ""
        last_stream_update = 0

        async for chunk_text in agent_orchestrator.handle_message(ctx, message_history):
            final_text_response += chunk_text
            state["final_text"] = final_text_response
            state["last_update_time"] = time.time()

            # Update UI (Standard Stream)
            now = time.time()
            if now - last_stream_update > 1.0:  # Reduce frequency slightly
                msg_id = getattr(
                    thinking_msg, "message_id", getattr(thinking_msg, "id", None)
                )
                await ctx.edit_message(msg_id, final_text_response)
                last_stream_update = now

        # 停止动画
        state["running"] = False
        animation_task.cancel()  # Ensure it stops immediately

        # 5. 发送最终回复并入库
        # 5. 发送最终回复并入库
        if final_text_response:
            # 用户体验优化：为了避免工具产生的中间消息导致最终结果被顶上去需要翻页，
            # 这里改为发送一条新消息作为最终结果，并删除原本的"思考中"消息。

            # 1. 发送新消息
            sent_msg = await ctx.reply(final_text_response)

            # 2. 尝试删除旧的思考消息 (如果发送成功)
            if sent_msg:
                try:
                    await thinking_msg.delete()
                except Exception as del_e:
                    logger.warning(f"Failed to delete thinking_msg: {del_e}")
            else:
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

            if sent_msg and final_display_text != final_text_response:
                msg_id = getattr(sent_msg, "message_id", getattr(sent_msg, "id", None))
                await ctx.edit_message(msg_id, final_display_text)

            # 记录统计
            await increment_stat(user_id, "ai_chats")
    except Exception as e:
        state["running"] = False
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
    thinking_msg = await ctx.reply("🔍 正在分析图片...")

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
    thinking_msg = await ctx.reply("🎬 正在分析视频，这可能需要一些时间...")

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
