import time
import logging
import base64
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import BadRequest

from core.config import gemini_client, GEMINI_MODEL
from services.web_summary_service import extract_urls, summarize_webpage, is_video_platform, fetch_webpage_content
from user_context import get_user_context, add_message
from repositories import get_user_settings, get_video_cache
from utils import smart_edit_text, smart_reply_text
from stats import increment_stat

logger = logging.getLogger(__name__)

# 思考提示消息
THINKING_MESSAGE = "🤔 正在思考中..."


async def handle_ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    处理普通文本消息，使用 Gemini AI 生成回复
    支持引用（回复）包含图片或视频的消息
    """
    user_message = update.message.text
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id

    if not user_message:
        return

    # 检查用户权限
    from core.config import is_user_allowed
    if not await is_user_allowed(user_id):
        await smart_reply_text(update,
            "⛔ 抱歉，您没有使用 AI 对话功能的权限。\n\n"
            "如需下载视频，请使用 /download 命令。"
        )
        return

    # 检查消息中是否包含 URL（自动生成网页摘要）
    urls = extract_urls(user_message)
    
    # 如果只是一个 URL 且没有其他内容
    if urls and user_message.strip() in urls:
        url = urls[0]
        
        # 智能逻辑：如果是视频平台链接，询问用户意图
        if is_video_platform(url):
            context.user_data['pending_video_url'] = url
            
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = [
                [
                    InlineKeyboardButton("📹 下载视频", callback_data="action_download_video"),
                    InlineKeyboardButton("📝 AI 摘要", callback_data="action_summarize_video"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await smart_reply_text(update,
                "🤔 检测到视频链接，您想要做什么？",
                reply_markup=reply_markup
            )
            return

        # 普通网页，直接生成摘要
        thinking_msg = await smart_reply_text(update, "📄 正在获取网页内容并生成摘要...")
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        
        summary = await summarize_webpage(url)
        # Use smart_edit_text which handles Markdown conversion and fallbacks
        await smart_edit_text(thinking_msg, summary)
        
        # 记录统计
        await increment_stat(user_id, "ai_chats")
        return

    # 检查是否开启了沉浸式翻译
    settings = await get_user_settings(user_id)
    if settings.get("auto_translate", 0):
        # 翻译模式开启
        thinking_msg = await smart_reply_text(update, "🌍 翻译中...")
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        
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
                await smart_edit_text(thinking_msg, f"🌍 **译文**\n\n{response.text}")
                # 统计
                await increment_stat(user_id, "translations_count")
            if response.text:
                await smart_edit_text(thinking_msg, f"🌍 **译文**\n\n{response.text}")
                # 统计
                await increment_stat(user_id, "translations_count")
            else:
                await smart_edit_text(thinking_msg, "❌ 无法翻译。")
        except Exception as e:
            logger.error(f"Translation error: {e}")
            await smart_edit_text(thinking_msg, "❌ 翻译服务出错。")
        return

    # --- Skill Router (优先匹配用户自定义 Skill) ---
    from core.skill_router import skill_router
    from core.skill_loader import skill_loader
    
    skill_name, skill_params = await skill_router.route(user_message)
    
    if skill_name:
        logger.info(f"Skill Matched: {skill_name} | params={skill_params}")
        
        # 加载并执行 Skill
        skill_module = skill_loader.load_skill(skill_name)
        if skill_module and hasattr(skill_module, 'execute'):
            try:
                await skill_module.execute(update, context, skill_params)
                await increment_stat(user_id, "ai_chats")
                return
            except Exception as e:
                logger.error(f"Skill execution error: {e}")
                await smart_reply_text(update, f"❌ Skill 执行失败：{e}")
                return
    
    # --- Smart Intent Routing (Fallback to built-in intents) ---
    # Save the user message to history immediately (important for context)
    add_message(context, "user", user_message)

    from services.intent_router import analyze_intent, UserIntent
    
    # Analyze intent
    # We pass the user message. The router uses a fast model to determine intent.
    intent_result = await analyze_intent(user_message)
    intent = intent_result.get("intent")
    params = intent_result.get("params", {})
    
    logger.info(f"Smart Routing: {intent} | params={params}")

    if intent == UserIntent.DOWNLOAD_VIDEO:
        # 尝试从 params 获取 URL，或者回退到 extract_urls
        target_url = params.get("url")
        if not target_url:
             # Fallback extraction
            found_urls = extract_urls(user_message)
            if found_urls:
                target_url = found_urls[0]
        
        if target_url:
            # await update.message.reply_text(f"🚀 识别到下载意图，正在处理链接：{target_url}")
            from .media_handlers import process_video_download
            # Force non-audio-only (default) unless specified (could extend router to detect audio only)
            # For now, default to video.
            await process_video_download(update, context, target_url, audio_only=False)
            return
        else:
             # 如果意图是下载但没找到 URL，可能用户只说了"下载视频"但没给连接。
             # 此时让其进入常规对话，或者由 Gemini 回复询问。
             pass

    elif intent == UserIntent.GENERATE_IMAGE:
        prompt = params.get("prompt")
        if not prompt:
            prompt = user_message # Fallback to full message
            
        # await update.message.reply_text(f"🎨 识别到画图意图，正在生成：{prompt}")
        from image_generator import handle_image_generation
        await handle_image_generation(update, context, prompt)
        return

    elif intent == UserIntent.SET_REMINDER:
        time_str = params.get("time")
        content = params.get("content")
        
        if time_str and content:
            from .service_handlers import process_remind
            await process_remind(update, context, time_str, content)
            return
        else:
             # Missing params, fallback to Chat or ask user
             pass

    elif intent == UserIntent.RSS_SUBSCRIBE:
        url = params.get("url")
        if url:
             from .service_handlers import process_subscribe
             await process_subscribe(update, context, url)
             return

    elif intent == UserIntent.MONITOR_KEYWORD:
        keyword = params.get("keyword")
        if keyword:
             from .service_handlers import process_monitor
             await process_monitor(update, context, keyword)
             return

    elif intent == UserIntent.BROWSER_ACTION:
        from .mcp_handlers import handle_browser_action
        handled = await handle_browser_action(update, context, params)
        if handled:
            return
        # 如果未处理（如 MCP 禁用），回退到普通对话

    elif intent == UserIntent.STOCK_WATCH:
        action = params.get("action", "add")
        stock_name = params.get("stock_name", "")
        from .service_handlers import process_stock_watch
        await process_stock_watch(update, context, action, stock_name)
        return

    # ----------------------------

    # ----------------------------
    # 检查是否引用了包含媒体的消息
    from .message_utils import process_reply_message, process_and_send_code_files
    
    extra_context = "" 
    has_media, reply_extra_context, media_data, mime_type = await process_reply_message(update, context)
    
    # process_reply_message returns False if size limit exceeded or no media/reply
    # If returned False but we had a reply with media that was too big, we should probably stop?
    # Actually process_reply_message sends the warning itself.
    # However, if it returns False, it might mean "no reply" OR "failed".
    # We need to distinguish. 
    # But for now, if has_media is False and extra_context is empty, it means nothing happened.
    
    if reply_extra_context:
        extra_context += reply_extra_context
    
    # Need to handle the case where process_reply_message aborted (e.g. file too big)
    # Since we can't easily signal "abort" vs "nothing found" with current signature without checking logs or changing sign.
    # But wait, if process_reply_message sends a message "File too big", we should probably return here.
    # Check if update.message.reply_to_message exists but has_media is False and we expected it?
    # Simple check: If reply_to had video/audio but has_media is False, then we aborted.
    if update.message.reply_to_message:
         r = update.message.reply_to_message
         if (r.video or r.audio or r.voice) and not has_media:
             # Likely aborted due to size limit
             return
    
    # 3. 检查当前消息中是否有 URL (混合文本情况)
    # 如果 extra_context 为空（说明没有 Reply URL），且 urls 不为空（说明当前消息有 URL）
    if not extra_context and urls:
        status_msg = await smart_reply_text(update, "📄 正在获取网页内容...")
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        
        try:
            # 获取第一个 URL 的内容
            web_content = await fetch_webpage_content(urls[0])
            
            if web_content:
                extra_context = f"【网页内容】\n{web_content}\n\n"
            else:
                logger.warning(f"Failed to fetch content for mixed URL: {urls[0]}")
                extra_context = "【系统提示】检测到链接，但无法读取其内容（可能是反爬虫限制）。请仅根据 URL 标题或从 URL 本身推测（如果可能），并告知用户无法读取详情。\n\n"
            
        except Exception as e:
            logger.error(f"Error fetching mixed URL: {e}")
        
        # 无论成功失败，删除因为 fetch 而产生的提示消息
        try:
            await status_msg.delete()
        except:
            pass

    if not has_media:
        # 普通文本对话
        thinking_msg = await smart_reply_text(update, THINKING_MESSAGE)
    
    # 将网页上下文合并到用户消息中
    if extra_context:
        user_message = extra_context + "用户请求：" + user_message

    # 发送"正在输入"状态
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        if has_media and media_data:
            # 带媒体的请求
            contents = [
                {
                    "parts": [
                        {"text": user_message},
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": base64.b64encode(bytes(media_data)).decode("utf-8"),
                            }
                        },
                    ]
                }
            ]
            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=contents,
                config={
                    "system_instruction": "你是一个友好的助手，可以分析图片和视频内容并回答问题。请用中文回复。",
                },
            )
            if response.text:
                await smart_edit_text(thinking_msg, response.text)
            else:
                await smart_edit_text(thinking_msg, "抱歉，我无法分析这个内容。")
        else:
            # 纯文本对话（流式响应 + 多轮上下文）
            
            # 1. 用户消息已在 intent routing 入口处保存，此处不再重复保存
            
            # -----------------------------------------------------------------
            # 2. 构建上下文
            context_messages = []
            
            # A. 如果是回复某个消息 --> 仅使用该消息 + 当前消息
            reply_to = update.message.reply_to_message
            if reply_to:
                reply_id = reply_to.message_id
                logger.info(f"User replied to message {reply_id}")
                
                # 直接使用 Telegram 消息对象的内容
                replied_content = reply_to.text or reply_to.caption
                
                if replied_content:
                    context_messages.append({
                        "role": "user",  # 被回复的消息作为上一个 user message 或者 model message
                        "parts": [{"text": f"【引用内容】\n{replied_content}"}] 
                    })
                else:
                    logger.info("Replied message has no text content.")
            
            # B. 如果不是回复 --> 使用最近的历史记录
            else:
                context_messages = get_user_context(context)
            
            # append current user message
            context_messages.append({
                "role": "user",
                "parts": [{"text": user_message}]
            })

            # -----------------------------------------------------------------
            # 4. 生成回复 (Delegated to AiService)
            from services.ai_service import AiService
            ai_service = AiService()
            
            # Determine if memory tools should be enabled
            # Only enable memory for explicit MEMORY_RECALL intent or naturally broad conversations?
            # User request: "先判断是否需要调取记忆"
            # For now, strict: only MEMORY_RECALL enables memory tools.
            # This avoids "always talking about Wuxi".
            # Note: intent variable is available from earlier scope
            
            enable_memory = (intent == UserIntent.MEMORY_RECALL)
            if enable_memory:
                 logger.info(f"Memory tools enabled for intent: {intent}")
            
            final_text_response = ""
            last_update_time = 0
            
            async for chunk_text in ai_service.generate_response_stream(user_id, context_messages, enable_memory=enable_memory):
                final_text_response += chunk_text
                
                # Update typing status / message
                now = time.time()
                if now - last_update_time > 0.8:
                    await smart_edit_text(thinking_msg, final_text_response)
                    last_update_time = now

            # -----------------------------------------------------------------
            # 5. 发送最终回复并入库
            if final_text_response:
                # smart_edit_text handles markdown formatting and errors
                sent_msg = await smart_edit_text(thinking_msg, final_text_response)
                
                # 记录模型回复到上下文
                add_message(context, "model", final_text_response)
                
                # Try to extract code blocks, send as files, and get truncated text
                final_display_text = await process_and_send_code_files(update, context, final_text_response)
                
                # Update the message with cleaned display text
                if sent_msg:
                     await smart_edit_text(sent_msg, final_display_text)

                # 记录统计
                await increment_stat(user_id, "ai_chats")
            else:
                await smart_edit_text(thinking_msg, "抱歉，我无法生成回复。请稍后再试。")

    except Exception as e:
        logger.error(f"AI chat error: {e}")
        await smart_edit_text(thinking_msg,
            "❌ AI 服务出现错误，请稍后再试。\n\n"
            "如需下载视频，请点击 /download 进入下载模式。"
        )


async def handle_ai_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    处理图片消息，使用 Gemini AI 分析图片
    """
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    
    # 检查用户权限
    from core.config import is_user_allowed
    if not await is_user_allowed(user_id):
        await smart_reply_text(update,
            "⛔ 抱歉，您没有使用 AI 功能的权限。"
        )
        return
    
    # 获取图片（选择最大分辨率）
    photo = update.message.photo[-1]
    caption = update.message.caption or "请描述这张图片"

    # Save to history immediately
    add_message(context, "user", f"【用户发送了一张图片】 {caption}")
    
    # 立即发送"正在分析"提示
    thinking_msg = await smart_reply_text(update, "🔍 正在分析图片...")
    
    # 发送"正在输入"状态
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    
    try:
        # 下载图片
        file = await context.bot.get_file(photo.file_id)
        image_bytes = await file.download_as_bytearray()
        
        # 构建带图片的内容
        contents = [
            {
                "parts": [
                    {"text": caption},
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": base64.b64encode(bytes(image_bytes)).decode("utf-8"),
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
            display_text = await process_and_send_code_files(update, context, response.text)
            
            # 更新消息
            await smart_edit_text(thinking_msg, display_text)
            
            # Save model response to history
            add_message(context, "model", response.text)
            
            # 记录统计
            await increment_stat(user_id, "photo_analyses")

        else:
            await smart_edit_text(thinking_msg, "抱歉，我无法分析这张图片。请稍后再试。")
        
    except Exception as e:
        logger.error(f"AI photo analysis error: {e}")
        await smart_edit_text(thinking_msg, "❌ 图片分析失败，请稍后再试。")


async def handle_ai_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    处理视频消息，使用 Gemini AI 分析视频
    """
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    
    # 检查用户权限
    from core.config import is_user_allowed
    if not await is_user_allowed(user_id):
        await smart_reply_text(update,
            "⛔ 抱歉，您没有使用 AI 功能的权限。"
        )
        return
    
    # 获取视频
    video = update.message.video
    if not video:
        return
    
    caption = update.message.caption or "请分析这个视频的内容"
    
    # 检查视频大小（Gemini 有限制）
    # 检查视频大小（Gemini 有限制）
    if video.file_size and video.file_size > 20 * 1024 * 1024:  # 20MB 限制
        await smart_reply_text(update,
            "⚠️ 视频文件过大（超过 20MB），无法分析。\n\n"
            "请尝试发送较短的视频片段。"
        )
        return
    
    # 立即发送"正在分析"提示
    # 立即发送"正在分析"提示
    thinking_msg = await smart_reply_text(update, "🎬 正在分析视频，这可能需要一些时间...")
    
    # 发送"正在输入"状态
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    
    try:
        # 下载视频
        file = await context.bot.get_file(video.file_id)
        video_bytes = await file.download_as_bytearray()
        
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
                            "data": base64.b64encode(bytes(video_bytes)).decode("utf-8"),
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
            display_text = await process_and_send_code_files(update, context, response.text)
            
            # Update the thinking message with the cleaned text
            await smart_edit_text(thinking_msg, display_text)
            
            # 记录统计
            await increment_stat(user_id, "video_analyses")
        else:
            await smart_edit_text(thinking_msg, "抱歉，我无法分析这个视频。请稍后再试。")
        
    except Exception as e:
        logger.error(f"AI video analysis error: {e}")
        await smart_edit_text(thinking_msg,
            "❌ 视频分析失败，请稍后再试。\n\n"
            "可能的原因：\n"
            "• 视频格式不支持\n"
            "• 视频时长过长\n"
            "• 服务暂时不可用"
        )
