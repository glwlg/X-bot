import time
import logging
import base64
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import BadRequest

from config import gemini_client, GEMINI_MODEL
from web_summary import extract_urls, summarize_webpage, is_video_platform, fetch_webpage_content
from user_context import get_user_context, add_message
from database import get_chat_message, get_user_settings, get_video_cache
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
    from config import is_user_allowed
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

    # --- Smart Intent Routing ---
    from intent_router import analyze_intent, UserIntent
    
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

    # ----------------------------

    # 检查是否引用了包含媒体的消息
    reply_to = update.message.reply_to_message
    has_media = False
    media_data = None
    mime_type = None
    extra_context = ""
    
    if reply_to:
        # 1. 尝试提取引用消息中的 URL 并获取内容
        reply_urls = []
        
        # DEBUG LOG
        logger.info(f"Checking reply_to message {reply_to.message_id} for URLs")
        
        # A. 从实体（超链接/文本链接）提取
        if reply_to.entities:
            for entity in reply_to.entities:
                logger.info(f"Found text entity: {entity.type} at offset {entity.offset}")
                if entity.type == "text_link":
                    reply_urls.append(entity.url)
                elif entity.type == "url":
                    reply_urls.append(reply_to.parse_entity(entity))

        if reply_to.caption_entities:
            for entity in reply_to.caption_entities:
                logger.info(f"Found caption entity: {entity.type} at offset {entity.offset}")
                if entity.type == "text_link":
                    reply_urls.append(entity.url)
                elif entity.type == "url":
                    reply_urls.append(reply_to.parse_caption_entity(entity))
                
        # B. 从文本正则提取 (兜底，防止实体未解析)
        if not reply_urls:
            reply_text = reply_to.text or reply_to.caption or ""
            found = extract_urls(reply_text)
            logger.info(f"Regex found URLs: {found}")
            reply_urls = found
        
        # 去重
        reply_urls = list(set(reply_urls))
        logger.info(f"Final detected reply_urls: {reply_urls}")

        if reply_urls:
            # 发现 URL，尝试获取内容
            # 先发送一个提示，避免用户以为卡死
            status_msg = await smart_reply_text(update, "📄 正在获取引用网页内容...")
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            
            try:
                web_content = await fetch_webpage_content(reply_urls[0])
                if web_content:
                    extra_context = f"【引用网页内容】\n{web_content}\n\n"
                    # 获取成功，删除提示消息
                    await status_msg.delete()
                else:
                    # 获取失败，提示 AI 告知用户
                    extra_context = (
                        "【系统提示】引用的网页链接无法访问（无法提取内容，可能是反爬虫限制）。"
                        "请在回答中明确告知用户你无法读取该链接的内容，并仅根据现有的文本信息进行回答。"
                        "\n\n"
                    )
                    await status_msg.delete()
            except Exception as e:
                logger.error(f"Error fetching reply URL: {e}")
                # 出错也提示 AI
                extra_context = "【系统提示】读取链接时发生错误。请告知用户无法访问该链接。\n\n"
                await status_msg.delete()

        # 2. 处理媒体
        if reply_to.video:
            has_media = True
            video = reply_to.video
            file_id = video.file_id
            mime_type = video.mime_type or "video/mp4"
            
            # 优先检查本地缓存
            cache_path = await get_video_cache(file_id)
            
            if cache_path:
                import os
                if os.path.exists(cache_path):
                    logger.info(f"Using cached video: {cache_path}")
                    thinking_msg = await smart_reply_text(update, "🎬 正在分析视频（使用缓存）...")
                    with open(cache_path, "rb") as f:
                        media_data = bytearray(f.read())
                else:
                    # 缓存文件不存在
                    pass 
            
            # 缓存未命中，通过 Telegram API 下载
            if media_data is None:
                # 检查大小限制（Telegram API 限制 20MB）
                if video.file_size and video.file_size > 20 * 1024 * 1024:
                    await smart_reply_text(update,
                        "⚠️ 引用的视频文件过大（超过 20MB），无法通过 Telegram 下载分析。\n\n"
                        "提示：Bot 下载的视频会被缓存，可以直接分析。"
                    )
                    return
                thinking_msg = await smart_reply_text(update, "🎬 正在下载并分析视频...")
                file = await context.bot.get_file(video.file_id)
                media_data = await file.download_as_bytearray()
                
        elif reply_to.photo:
            has_media = True
            photo = reply_to.photo[-1]
            mime_type = "image/jpeg"
            thinking_msg = await smart_reply_text(update, "🔍 正在分析图片...")
            file = await context.bot.get_file(photo.file_id)
            media_data = await file.download_as_bytearray()

        elif reply_to.audio or reply_to.voice:
            has_media = True
            if reply_to.audio:
                file_id = reply_to.audio.file_id
                mime_type = reply_to.audio.mime_type or "audio/mpeg"
                file_size = reply_to.audio.file_size
                label = "音频"
            else:
                file_id = reply_to.voice.file_id
                mime_type = reply_to.voice.mime_type or "audio/ogg"
                file_size = reply_to.voice.file_size
                label = "语音"

            # Check size limit (20MB)
            if file_size and file_size > 20 * 1024 * 1024:
                await smart_reply_text(update,
                    f"⚠️ 引用的{label}文件过大（超过 20MB），无法通过 Telegram 下载分析。"
                )
                return

            thinking_msg = await smart_reply_text(update, f"🎧 正在分析{label}...")
            file = await context.bot.get_file(file_id)
            media_data = await file.download_as_bytearray()
    
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
            
            # 1. 保存当前用户消息
            current_msg_id = update.message.message_id
            await add_message(user_id, "user", user_message, message_id=current_msg_id)
            
            # -----------------------------------------------------------------
            # 2. 构建上下文
            context_messages = []
            
            # A. 如果是回复某个消息 --> 仅使用该消息 + 当前消息
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
                context_messages = await get_user_context(user_id)
            
            # append current user message
            context_messages.append({
                "role": "user",
                "parts": [{"text": user_message}]
            })

            # -----------------------------------------------------------------
            # 3. 准备工具 (MCP Memory)
            from config import MCP_MEMORY_ENABLED
            tools_config = None
            
            if MCP_MEMORY_ENABLED:
                try:
                    from mcp_client import mcp_manager
                    from mcp_client.tools_bridge import convert_mcp_tools_to_gemini
                    from mcp_client.memory import register_memory_server
                    
                    # 确保 Memory Server 类已注册
                    register_memory_server()
                    
                    # 获取该用户专属的 Memory Server 实例
                    # mcp_manager.get_server 会为每个 user_id 创建/复用独立的实例
                    # 实例 Key 如: memory_12345
                    memory_server = await mcp_manager.get_server("memory", user_id=user_id)
                    
                    if memory_server and memory_server.session:
                        # 主动列出工具
                        mcp_tools_result = await memory_server.session.list_tools()
                        gemini_funcs = convert_mcp_tools_to_gemini(mcp_tools_result.tools)
                        
                        # 按 Gemini 格式包装
                        if gemini_funcs:
                            tools_config = [{"function_declarations": gemini_funcs}]
                            logger.info(f"Injected {len(gemini_funcs)} memory tools into Gemini for user {user_id}.")
                except Exception as e:
                    logger.error(f"Failed to setup memory tools: {e}")

            # -----------------------------------------------------------------
            # 4. 生成回复 (支持 Function Calling 循环)
            
            # 定义最大循环次数防止死循环
            MAX_TURNS = 5
            turn_count = 0
            final_text_response = ""
            
            while turn_count < MAX_TURNS:
                turn_count += 1
                
                # 如果有 tools，首轮使用非流式以支持 Function Calling
                # 如果 tools_config 为空，则回退到流式
                if tools_config:
                    response = gemini_client.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=context_messages,
                        config={
                            "system_instruction": (
                                "你是一个友好的助手。请用中文回复。\n\n"
                                "【记忆管理指南】\n"
                                "请遵循以下步骤进行交互：\n\n"
                                "1. **身份识别**：\n"
                                "   - 始终将当前交互用户视为实体 'User'。\n\n"
                                "2. **记忆检索（Memory Retrieval）**：\n"
                                "   - 在回答之前，积极使用 `open_nodes(names=['User'])` 检索关于 'User' 的所有上下文信息。\n"
                                "   - 如果遇到特定话题，也可以通过关键词搜索相关节点。\n\n"
                                "3. **记忆更新（Memory Update）**：\n"
                                "   - 在对话中时刻关注以下类别的新信息：\n"
                                "     a) **基本身份**：年龄、性别、居住地（Location）、职业等。\n"
                                "     b) **行为习惯**、**偏好**、**目标**、**关系**等。\n\n"
                                "   - 当捕获到新信息时：\n"
                                "     a) 使用 `create_entities` 为重要的人、地点、组织创建实体。\n"
                                "     b) 使用 `create_relations` 将它们连接到 'User'（例如：Relation('User', 'lives in', '无锡')）。\n"
                                "     c) 使用 `add_observations` 存储具体的观察事实。\n"
                            ),
                            "tools": tools_config
                        },
                    )
                    
                    # 检查是否有 function call
                    # Gemini Python SDK genai.types structure:
                    # response.candidates[0].content.parts[0].function_call
                    function_calls = []
                    
                    if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                        for part in response.candidates[0].content.parts:
                            if part.function_call:
                                function_calls.append(part.function_call)
                    
                    if function_calls:
                        # 有工具调用请求
                        logger.info(f"AI requested function calls: {[fc.name for fc in function_calls]}")
                        
                        # 1. 将模型回复（包含 function_call）加入历史
                        context_messages.append(response.candidates[0].content)
                        
                        # 2. 执行所有工具
                        for fc in function_calls:
                            tool_name = fc.name
                            tool_args = fc.args
                            
                            logger.info(f"Executing tool: {tool_name} args={tool_args}")
                            
                            tool_result_content = {}
                            try:
                                # 执行 MCP 工具
                                # 注意: memory server 的 override 已经在 call_tool 内部处理好了 schema 校验问题
                                
                                # 使用 mcp_manager.call_tool 需要知道准确的 instance_key
                                # 或者直接使用我们上面获取到的 memory_server 实例 (如果在 scope 内)
                                # 之前我们在 scope 435行左右获取了 memory_server。
                                # 但是该变量在 while 循环之外。
                                # Python 变量作用域在函数内是可见的。
                                
                                # 但是，如果 multiple servers (e.g. playwright + memory), 需要区分。
                                # Playwright 工具不是 memory 工具。
                                # 简单判断：如果 tool_name 在 memory tools 中，则调 memory_server。
                                # 目前 tools_config 只有 memory。
                                
                                # 为了健壮性，我们可以检查 tool_name 是否属于 memory_server 的 capabilities?
                                # 或者简单地：当前场景我们只注入了 memory tools。
                                
                                if memory_server:
                                     raw_result = await memory_server.call_tool(tool_name, tool_args)
                                else:
                                     # Fallback (unlikely)
                                     raw_result = await mcp_manager.call_tool("memory", tool_name, tool_args)
                                
                                tool_result_content = {"result": raw_result}
                            except Exception as e:
                                logger.error(f"Tool execution failed: {e}")
                                tool_result_content = {"error": str(e)}
                                
                            # 3. 将工具结果（FunctionResponse）加入历史
                            context_messages.append({
                                "role": "tool", # Gemini SDK 期望 role="tool"
                                "parts": [{
                                    "function_response": {
                                        "name": tool_name,
                                        "response": tool_result_content
                                    }
                                }]
                            })
                            
                        # 继续下一轮循环，把工具结果发回给模型
                        continue
                        
                    else:
                        # 没有工具调用，这是最终回复
                        # 提取文本
                        if response.text:
                            final_text_response = response.text
                        else:
                            final_text_response = "（无文本回复）"
                        break
                        
                else:
                    # 没有工具配置，走原来的流式逻辑
                    response = gemini_client.models.generate_content_stream(
                        model=GEMINI_MODEL,
                        contents=context_messages,
                        config={
                            "system_instruction": "你是一个友好的助手，可以帮助用户解答问题。请用中文回复。",
                        },
                    )
                    
                    # 流式处理
                    last_update_time = 0
                    for chunk in response:
                        if chunk.text:
                            final_text_response += chunk.text
                            # 每 0.8 秒更新一次消息 (流式模式下)
                            now = time.time()
                            if now - last_update_time > 0.8:
                                await smart_edit_text(thinking_msg, final_text_response)
                                last_update_time = now
                    break

            # -----------------------------------------------------------------
            # 5. 发送最终回复并入库
            if final_text_response:
                # smart_edit_text handles markdown formatting and errors
                sent_msg = await smart_edit_text(thinking_msg, final_text_response)
                
                if sent_msg:
                    await add_message(user_id, "model", final_text_response, message_id=sent_msg.message_id)
                else:
                    await add_message(user_id, "model", final_text_response)

                # 记录统计
                await increment_stat(user_id, "ai_chats")
            else:
                await smart_edit_text(thinking_msg, "抱歉，我无法生成回复。请稍后再试。")

    except Exception as e:
        logger.error(f"AI chat error: {e}")
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
    from config import is_user_allowed
    from config import is_user_allowed
    if not is_user_allowed(user_id):
        await smart_reply_text(update,
            "⛔ 抱歉，您没有使用 AI 功能的权限。"
        )
        return
    
    # 获取图片（选择最大分辨率）
    photo = update.message.photo[-1]
    caption = update.message.caption or "请描述这张图片"
    
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
            await smart_edit_text(thinking_msg, response.text)
            # 记录统计
            await increment_stat(user_id, "photo_analyses")
        if response.text:
            await smart_edit_text(thinking_msg, response.text)
            # 记录统计
            await increment_stat(user_id, "photo_analyses")
        else:
            await smart_edit_text(thinking_msg, "抱歉，我无法分析这张图片。请稍后再试。")
        
    except Exception as e:
        logger.error(f"AI photo analysis error: {e}")
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
    from config import is_user_allowed
    from config import is_user_allowed
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
            await smart_edit_text(thinking_msg, response.text)
            # 记录统计
            await increment_stat(user_id, "video_analyses")
        else:
            await smart_edit_text(thinking_msg, "抱歉，我无法分析这个视频。请稍后再试。")
        
    except Exception as e:
        logger.error(f"AI video analysis error: {e}")
    except Exception as e:
        logger.error(f"AI video analysis error: {e}")
        await smart_edit_text(thinking_msg,
            "❌ 视频分析失败，请稍后再试。\n\n"
            "可能的原因：\n"
            "• 视频格式不支持\n"
            "• 视频时长过长\n"
            "• 服务暂时不可用"
        )
