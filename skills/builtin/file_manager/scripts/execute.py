"""
File Manager Skill Script
"""

import os
import logging
import asyncio
import aiofiles
from core.platform.models import UnifiedContext

logger = logging.getLogger(__name__)


async def execute(ctx: UnifiedContext, params: dict, runtime=None) -> str:
    """执行文件管理操作"""
    action = params.get("action", "list")
    path = params.get("path", ".")
    content = params.get("content")

    # Resolve path to be safe-ish (though user has full control usually in this context)
    # Defaulting relative paths to current working directory
    if not os.path.isabs(path):
        current_dir = os.getcwd()
        path = os.path.join(current_dir, path)

    if action == "list":
        return await list_files(ctx, path)

    if action == "read":
        return await read_file(ctx, path)

    if action == "write":
        if content is None:
            return {"text": "❌ 写入文件需要提供 `content` 内容。", "ui": {}}
        return await write_file(ctx, path, content)

    if action == "delete":
        return await delete_file(ctx, path)

    if action == "send":
        return await send_file(ctx, path)

    return {"text": f"❌ 未知操作: {action}", "ui": {}}


async def list_files(ctx: UnifiedContext, path: str):
    """List files in directory"""
    if not os.path.exists(path):
        return {"text": f"❌ 路径不存在: `{path}`", "ui": {}}

    if not os.path.isdir(path):
        return {"text": f"❌ 路径不是目录: `{path}`", "ui": {}}

    try:
        # Run os.listdir in thread pool
        loop = asyncio.get_running_loop()
        items = await loop.run_in_executor(None, os.listdir, path)

        # Sort items: directories first, then files
        dirs = []
        files = []
        for item in items:
            item_path = os.path.join(path, item)
            if os.path.isdir(item_path):
                dirs.append(item + "/")
            else:
                files.append(item)

        dirs.sort()
        files.sort()

        result_msg = f"📂 **目录列表**: `{path}`\n\n"
        if dirs:
            result_msg += (
                "**Folders:**\n" + "\n".join([f"- `{d}`" for d in dirs]) + "\n\n"
            )
        if files:
            result_msg += "**Files:**\n" + "\n".join([f"- `{f}`" for f in files])

        if not dirs and not files:
            result_msg += "(空目录)"

        return {"text": result_msg, "ui": {}}
    except Exception as e:
        logger.error(f"List files error: {e}")
        return {"text": f"❌ 无法列出目录: {str(e)}", "ui": {}}


async def read_file(ctx: UnifiedContext, path: str):
    """Read file content"""
    if not os.path.exists(path):
        return {"text": f"❌ 文件不存在: `{path}`", "ui": {}}

    if os.path.isdir(path):
        return {"text": f"❌ `{path}` 是一个目录，请使用 'list' 操作。", "ui": {}}

    try:
        async with aiofiles.open(path, mode="r", encoding="utf-8") as f:
            content = await f.read()

        # Truncate if too long for message
        max_len = 3000
        if len(content) > max_len:
            content = (
                content[:max_len]
                + f"\n... (内容过长，仅显示前 {max_len} 字符，请使用 'send' 获取完整文件)"
            )

        return {"text": f"📄 **文件内容**: `{path}`\n```\n{content}\n```", "ui": {}}
    except UnicodeDecodeError:
        return {
            "text": f"❌ 无法读取该文件 (可能是二进制文件)，请尝试使用 'send' 发送。",
            "ui": {},
        }
    except Exception as e:
        logger.error(f"Read file error: {e}")
        return {"text": f"❌ 读取文件失败: {str(e)}", "ui": {}}


async def write_file(ctx: UnifiedContext, path: str, content: str):
    """Write content to file"""
    try:
        # Ensure directory exists
        dirname = os.path.dirname(path)
        if dirname and not os.path.exists(dirname):
            os.makedirs(dirname, exist_ok=True)

        async with aiofiles.open(path, mode="w", encoding="utf-8") as f:
            await f.write(content)

        return {"text": f"✅ 已成功写入文件: `{path}`", "ui": {}}
    except Exception as e:
        logger.error(f"Write file error: {e}")
        return {"text": f"❌ 写入文件失败: {str(e)}", "ui": {}}


async def delete_file(ctx: UnifiedContext, path: str):
    """Delete a file"""
    if not os.path.exists(path):
        return {"text": f"❌ 文件不存在: `{path}`", "ui": {}}

    try:
        if os.path.isdir(path):
            return {
                "text": f"❌ `{path}` 是一个目录。本技能暂不支持删除目录。",
                "ui": {},
            }

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, os.remove, path)
        return {"text": f"🗑️ 已删除文件: `{path}`", "ui": {}}
    except Exception as e:
        logger.error(f"Delete file error: {e}")
        return {"text": f"❌ 删除失败: {str(e)}", "ui": {}}


async def send_file(ctx: UnifiedContext, path: str):
    """Send file to user"""
    if not os.path.exists(path):
        return {"text": f"❌ 文件不存在: `{path}`", "ui": {}}

    if os.path.isdir(path):
        return {"text": f"❌ `{path}` 是一个目录，无法直接发送。", "ui": {}}

    try:
        # Check size (e.g. 50MB limit)
        size = os.path.getsize(path)
        if size > 50 * 1024 * 1024:
            return {
                "text": f"❌ 文件过大 ({size / 1024 / 1024:.2f} MB)，无法通过 IM 发送。",
                "ui": {},
            }

        async with aiofiles.open(path, mode="rb") as f:
            data = await f.read()

        filename = os.path.basename(path)
        await ctx.reply_document(document=data, filename=filename, caption=f"📄 {path}")

        return {"text": f"✅ 已发送文件: `{path}`", "ui": {}}
    except Exception as e:
        logger.error(f"Send file error: {e}")
        return {"text": f"❌ 发送文件失败: {str(e)}", "ui": {}}


def register_handlers(adapter_manager):
    """Register command handlers"""
    from core.config import is_user_allowed

    async def cmd_ls(ctx):
        if not await is_user_allowed(ctx.message.user.id):
            return
        path = "."
        if ctx.message.text:
            parts = ctx.message.text.split()
            if len(parts) > 1:
                path = parts[1]
        return await list_files(ctx, path)

    async def cmd_cat(ctx):
        if not await is_user_allowed(ctx.message.user.id):
            return
        parts = ctx.message.text.split()
        if len(parts) < 2:
            await ctx.reply("Usage: /cat <file_path>")
            return
        return await read_file(ctx, parts[1])

    async def cmd_rm(ctx):
        if not await is_user_allowed(ctx.message.user.id):
            return
        parts = ctx.message.text.split()
        if len(parts) < 2:
            await ctx.reply("Usage: /rm <file_path>")
            return
        return await delete_file(ctx, parts[1])

    async def cmd_send(ctx):
        if not await is_user_allowed(ctx.message.user.id):
            return
        parts = ctx.message.text.split()
        if len(parts) < 2:
            await ctx.reply("Usage: /send_file <file_path>")
            return
        return await send_file(ctx, parts[1])

    # Register simple alias commands
    adapter_manager.on_command("ls", cmd_ls, description="列出文件")
    adapter_manager.on_command("cat", cmd_cat, description="读取文件")
    adapter_manager.on_command("rm", cmd_rm, description="删除文件")
    adapter_manager.on_command("send_file", cmd_send, description="发送文件")
