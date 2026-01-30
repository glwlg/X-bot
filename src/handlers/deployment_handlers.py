from core.platform.models import UnifiedContext
from services.deployment_service import docker_deployment_service

async def deploy_command(ctx: UnifiedContext):
    """
    Handle /deploy <github_url>
    """
    args = ctx.platform_ctx.args if ctx.platform_ctx else []
    if not args:
        await ctx.reply("⚠️ 请提供 GitHub 仓库地址。\n例如: `/deploy https://github.com/example/app`")
        return

    repo_url = args[0]
    if not repo_url.startswith("https://github.com/"):
        await ctx.reply("⚠️ 仅支持 GitHub 仓库 URL。")
        return

    # Initial status message
    status_msg = await ctx.reply(f"🚀 正在准备部署: {repo_url}\n⏳ 初始化中...")
    
    # Callback to update phase status (Clone, Plan, etc.) - New Message
    async def update_status(msg: str):
        try:
           # Preserving original behavior of sending new message for major status updates
           await ctx.reply(msg)
        except Exception:
            pass

    # Callback to update command logs (Docker build output) - Edit Message
    log_message = None
    async def progress_callback(msg: str):
        nonlocal log_message
        try:
            # If no log message exists, create one
            if not log_message:
                log_message = await ctx.reply(f"📋 **日志输出:**\n\n```\n{msg}\n```")
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

    # Pagination Log Strategy
    current_log_lines = []
    
    async def progress_callback_impl(chunk: str):
        nonlocal log_message, current_log_lines
        try:
            # Append new lines
            lines = chunk.splitlines()
            current_log_lines.extend(lines)
            
            # Construct content
            # Keep only the last 30 lines for the ACTIVE log message to simulate scrolling
            # Trying to keep the full history in one message is what causes "message too long" or weird edits.
            # If the user wants full history, we should send it as a file at the end.
            # But the requirement is "full execution history in chat".
            # So calls to edit must be careful.
            
            display_lines = current_log_lines
            
            # If total lines > 80, we split to a new message to avoid hitting limits
            if len(current_log_lines) > 60:
                # 1. Archive current message
                if log_message:
                     pass # Leave it as is
                
                # 2. Start new message with the overflow
                log_message = await ctx.reply("📋 **接上页日志...**")
                current_log_lines = [] # Reset buffer for new message
                display_lines = lines # New lines go to new message

            # Construct display text
            content_str = "\n".join(display_lines)
            display_text = f"📋 **执行日志:**\n\n```\n{content_str}\n```"
            
            if not log_message:
                log_message = await ctx.reply(display_text)
            else:
                await ctx.edit_message(log_message.message_id, display_text)
                
        except Exception:
             pass

    # Start deployment
    success, result = await docker_deployment_service.deploy_repository(
        repo_url, 
        update_callback=update_status,
        progress_callback=progress_callback_impl
    )
    
    # Final result
    await ctx.reply(result)
