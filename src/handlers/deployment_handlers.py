from telegram import Update
from telegram.ext import ContextTypes
from services.deployment_service import docker_deployment_service
from utils import smart_reply_text, smart_edit_text

async def deploy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle /deploy <github_url>
    """
    if not context.args:
        await smart_reply_text(update, "⚠️ 请提供 GitHub 仓库地址。\n例如: `/deploy https://github.com/example/app`")
        return

    repo_url = context.args[0]
    if not repo_url.startswith("https://github.com/"):
        await smart_reply_text(update, "⚠️ 仅支持 GitHub 仓库 URL。")
        return

    # Initial status message
    status_msg = await smart_reply_text(update, f"🚀 正在准备部署: {repo_url}\n⏳ 初始化中...")
    
    # Callback to update phase status (Clone, Plan, etc.) - New Message
    async def update_status(msg: str):
        try:
           await smart_reply_text(update, msg)
        except Exception:
            pass

    # Callback to update command logs (Docker build output) - Edit Message
    log_message = None
    async def progress_callback(msg: str):
        nonlocal log_message
        try:
            # If no log message exists, create one
            if not log_message:
                log_message = await smart_reply_text(update, f"📋 **日志输出:**\n\n```\n{msg}\n```")
            else:
                # Append/Update the existing log message
                # Note: To avoid "Message too long", we might only show the last N lines or rely on smart_edit_text truncation
                # For now, we just edit it. smart_edit_text handles truncation fallback.
                # However, constantly appending to a string that grows infinitely is bad for memory and API.
                # But here 'msg' from service is a CHUNK. We need to maintain the full text or just append?
                # The service sends chunks. We should probably append to the display logic.
                # ACTUALLY, simply editing the message with the NEW chunk replaces the content?
                # No, we want a scrolling log? 
                # Telegram can't scroll. We usually show the LAST N lines.
                # Let's change the strategy: The service sends chunks. 
                # Handler maintains a buffer of the last 20 lines.
                pass
                
            # Efficient Log Strategy:
            # We can't keep appending to the message text eternally.
            # We will maintain a buffer of the last 15 lines for display.
            # But the user wants "see every step".
            # If we overwrite, they lose history.
            # But Telegram message limit is 4096 chars.
            # So satisfying "every step" indefinitely in one message is impossible.
            # We will show the TAIL of the logs in the message.
        except Exception:
            pass

    # Better implementation of progress_callback with state
    log_buffer = ["...日志开始..."]
    
    async def progress_callback_impl(chunk: str):
        nonlocal log_message
        try:
            # Add new lines to buffer
            lines = chunk.splitlines()
            log_buffer.extend(lines)
            
            # Keep last 30 lines to fit in message (approx 2000 chars)
            display_lines = log_buffer[-30:]
            display_text = "📋 **执行日志:**\n\n<pre>" + "\n".join(display_lines) + "</pre>"
            
            if not log_message:
                log_message = await smart_reply_text(update, display_text)
            else:
                await smart_edit_text(log_message, display_text)
        except Exception:
             pass

    # Start deployment
    success, result = await docker_deployment_service.deploy_repository(
        repo_url, 
        update_callback=update_status,
        progress_callback=progress_callback_impl
    )
    
    # Final result
    await smart_reply_text(update, result)
