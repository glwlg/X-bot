from core.platform.models import UnifiedContext
from services.deployment_service import docker_deployment_service


async def deploy_command(ctx: UnifiedContext):
    """
    Handle /deploy <github_url>
    """
    args = ctx.platform_ctx.args if ctx.platform_ctx else []
    if not args:
        await ctx.reply(
            "⚠️ 请提供 GitHub 仓库地址。\n例如: `/deploy https://github.com/example/app`"
        )
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
                pass
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

            display_lines = current_log_lines

            # If total lines > 80, we split to a new message to avoid hitting limits
            if len(current_log_lines) > 60:
                # 1. Archive current message
                if log_message:
                    pass  # Leave it as is

                # 2. Start new message with the overflow
                log_message = await ctx.reply("📋 **接上页日志...**")
                current_log_lines = []  # Reset buffer for new message
                display_lines = lines  # New lines go to new message

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
        progress_callback=progress_callback_impl,
    )

    # Final result
    await ctx.reply(result)
