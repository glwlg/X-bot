"""
NotebookLM Skill - Google NotebookLM 自动化工具

基于 notebooklm-py CLI 实现，支持笔记本管理、来源添加、提问、生成播客/视频等功能。
"""
import asyncio
import json
import logging
import os
from telegram import Update
from telegram.ext import ContextTypes

from utils import smart_reply_text, smart_edit_text

logger = logging.getLogger(__name__)


SKILL_META = {
    "name": "notebooklm",
    "description": "Google NotebookLM 自动化工具。管理笔记本、添加来源、提问、生成播客/视频等。",
    "triggers": ["notebooklm", "笔记本", "notebook", "播客", "podcast", "下载播客", "下载视频", "生成播客", "生成视频"],
    "params": {
        "action": {
            "type": "str",
            "description": "操作类型: status, login, list, create, use, ask, source_add, source_list, source_fulltext, source_guide, generate_audio, generate_video, generate_quiz, artifact_list, artifact_wait, download, delete",
            "required": True
        },
        "notebook_id": {"type": "str", "description": "笔记本 ID"},
        "title": {"type": "str", "description": "笔记本标题（用于创建或查找）"},
        "question": {"type": "str", "description": "提问内容"},
        "source_url": {"type": "str", "description": "来源 URL（网页/YouTube/文件路径）"},
        "source_id": {"type": "str", "description": "来源 ID"},
        "source_ids": {"type": "list", "description": "多个来源 ID，用于指定提问或生成的来源范围"},
        "instructions": {"type": "str", "description": "生成指令（用于播客/视频）"},
        "artifact_id": {"type": "str", "description": "内容 ID（用于等待或下载）"},
        "artifact_type": {"type": "str", "description": "下载类型: audio, video, report, mind-map, data-table, quiz, flashcards"},
        "output_path": {"type": "str", "description": "下载输出路径"},
        "research_query": {"type": "str", "description": "网络研究查询"},
        "research_mode": {"type": "str", "description": "研究模式: fast, deep"},
        "new_conversation": {"type": "bool", "description": "是否开启新对话"},
    },
    "version": "2.0.0",
    "author": "system"
}


def _get_user_home(user_id: int) -> str:
    """获取用户特定的 NOTEBOOKLM_HOME 目录"""
    return f"/app/data/users/{user_id}/notebooklm"


async def _run_cli(args: list, user_id: int, timeout: int = 60) -> tuple:
    """运行 notebooklm CLI 命令"""
    home = _get_user_home(user_id)
    os.makedirs(home, exist_ok=True)
    
    env = os.environ.copy()
    env["NOTEBOOKLM_HOME"] = home
    
    cmd = ["notebooklm"] + args
    logger.info(f"Running: {' '.join(cmd)}")
    
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, stdout.decode(), stderr.decode()
    except asyncio.TimeoutError:
        return -1, "", f"Command timed out after {timeout}s"
    except Exception as e:
        return -1, "", str(e)


async def _find_notebook_id(user_id: int, title: str) -> str | None:
    """通过标题查找笔记本 ID"""
    code, stdout, _ = await _run_cli(["list", "--json"], user_id)
    if code == 0:
        try:
            data = json.loads(stdout)
            for nb in data.get("notebooks", []):
                if nb.get("title") == title:
                    return nb.get("id")
        except:
            pass
    return None


def _parse_error(stdout: str, stderr: str) -> str:
    """解析错误信息"""
    try:
        data = json.loads(stdout)
        if data.get("error"):
            msg = data.get("message", "Unknown error")
            if "expired" in msg.lower() or "redirect" in msg.lower():
                return (
                    "❌ **认证已过期**\n\n"
                    "请在本地电脑重新登录：\n"
                    "1. 运行 `notebooklm login`\n"
                    "2. 在浏览器中完成 Google 登录\n"
                    "3. 将 `storage_state.json` 发送给我\n\n"
                    "如未安装，先运行: `pip install notebooklm-py[browser]`"
                )
            return f"❌ 错误: {msg}"
    except:
        pass
    return f"❌ 失败:\n```\n{stderr or stdout or 'Unknown error'}\n```"


async def execute(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> str:
    """执行 NotebookLM 操作"""
    action = params.get("action", "").lower()
    user_id = update.effective_user.id
    
    if not action:
        return (
            "📚 **NotebookLM 可用操作:**\n\n"
            "• `status` - 查看认证状态\n"
            "• `login` - 登录指南\n"
            "• `list` - 列出所有笔记本\n"
            "• `create` - 创建新笔记本\n"
            "• `use` - 切换当前笔记本\n"
            "• `ask` - 向笔记本提问\n"
            "• `source_add` - 添加来源\n"
            "• `source_list` - 列出来源\n"
            "• `source_fulltext` - 获取来源全文\n"
            "• `source_guide` - 获取来源指南\n"
            "• `generate_audio` - 生成播客\n"
            "• `generate_video` - 生成视频\n"
            "• `generate_quiz` - 生成测验\n"
            "• `artifact_list` - 列出生成的内容\n"
            "• `download` - 下载内容\n"
            "• `delete` - 删除笔记本"
        )
    
    # ========== 认证相关 ==========
    if action == "status":
        code, stdout, stderr = await _run_cli(["status", "--json"], user_id)
        if code == 0:
            try:
                data = json.loads(stdout)
                if data.get("authenticated"):
                    nb = data.get("current_notebook")
                    if nb:
                        return f"✅ 已认证\n📓 当前笔记本: **{nb.get('title', 'Untitled')}**"
                    return "✅ 已认证，尚未选择笔记本"
                return "❌ 未认证。请使用 `login` 操作查看登录指南。"
            except:
                return f"📋 状态:\n```\n{stdout}\n```"
        return _parse_error(stdout, stderr)
    
    if action == "login":
        return (
            "🔐 **NotebookLM 登录指南**\n\n"
            "由于 Google 登录需要浏览器交互，请在**本地电脑**完成以下步骤：\n\n"
            "**步骤 1：安装 CLI 工具**\n"
            "```bash\n"
            "pip install notebooklm-py[browser]\n"
            "```\n\n"
            "**步骤 2：运行登录命令**\n"
            "```bash\n"
            "notebooklm login\n"
            "```\n\n"
            "**步骤 3：完成浏览器登录**\n"
            "• 会自动弹出浏览器窗口\n"
            "• 登录您的 Google 账户\n"
            "• 等待看到 NotebookLM 首页\n"
            "• 回到终端按 Enter 键\n\n"
            "**步骤 4：发送认证文件**\n"
            "将生成的文件发送给我：\n"
            "• Windows: `C:\\Users\\<用户名>\\.notebooklm\\storage_state.json`\n"
            "• macOS/Linux: `~/.notebooklm/storage_state.json`"
        )
    
    # ========== 笔记本管理 ==========
    if action == "list":
        code, stdout, stderr = await _run_cli(["list", "--json"], user_id)
        if code == 0:
            try:
                data = json.loads(stdout)
                if data.get("error"):
                    return _parse_error(stdout, stderr)
                notebooks = data.get("notebooks", [])
                if not notebooks:
                    return "📚 您还没有任何笔记本。使用 `create` 操作创建一个。"
                lines = ["📚 **您的笔记本列表:**\n"]
                for nb in notebooks:
                    lines.append(f"• **{nb.get('title') or '(无标题)'}**")
                    lines.append(f"  ID: `{nb.get('id')}`")
                return "\n".join(lines)
            except:
                return f"📋 笔记本列表:\n```\n{stdout}\n```"
        return _parse_error(stdout, stderr)
    
    if action == "create":
        title = params.get("title", "New Notebook")
        code, stdout, stderr = await _run_cli(["create", title, "--json"], user_id)
        if code == 0:
            try:
                data = json.loads(stdout)
                nb_id = data.get("id", "Unknown")
                return f"✅ 笔记本创建成功!\n• 标题: **{title}**\n• ID: `{nb_id}`"
            except:
                return f"✅ 创建成功:\n```\n{stdout}\n```"
        return _parse_error(stdout, stderr)
    
    if action == "use":
        notebook_id = params.get("notebook_id")
        if not notebook_id and params.get("title"):
            notebook_id = await _find_notebook_id(user_id, params["title"])
        if not notebook_id:
            return "❌ 请提供 notebook_id 或 title 参数"
        code, stdout, stderr = await _run_cli(["use", notebook_id], user_id)
        if code == 0:
            return f"✅ 已切换到笔记本: `{notebook_id}`"
        return _parse_error(stdout, stderr)
    
    if action == "delete":
        notebook_id = params.get("notebook_id")
        if not notebook_id:
            return "❌ 请提供 notebook_id 参数"
        code, stdout, stderr = await _run_cli(["notebook", "delete", notebook_id, "--json"], user_id)
        if code == 0:
            return f"✅ 笔记本已删除: `{notebook_id}`"
        return _parse_error(stdout, stderr)
    
    # ========== 提问 ==========
    if action == "ask":
        question = params.get("question")
        if not question:
            return "❌ 请提供 question 参数"
        
        args = ["ask", question, "--json"]
        
        # 获取 notebook_id
        notebook_id = params.get("notebook_id")
        if not notebook_id and params.get("title"):
            notebook_id = await _find_notebook_id(user_id, params["title"])
            if not notebook_id:
                return f"❌ 找不到名为 '{params['title']}' 的笔记本"
        if notebook_id:
            args.extend(["--notebook", notebook_id])
        
        # 指定来源
        source_ids = params.get("source_ids", [])
        for sid in source_ids:
            args.extend(["-s", sid])
        
        # 新对话
        if params.get("new_conversation"):
            args.append("--new")
        
        code, stdout, stderr = await _run_cli(args, user_id, timeout=120)
        if code == 0:
            try:
                data = json.loads(stdout)
                if data.get("error"):
                    return _parse_error(stdout, stderr)
                answer = data.get("answer", stdout)
                return f"💬 **回答:**\n\n{answer}"
            except:
                return f"💬 **回答:**\n\n{stdout}"
        return _parse_error(stdout, stderr)
    
    # ========== 来源管理 ==========
    if action == "source_add":
        source_url = params.get("source_url")
        if not source_url:
            return "❌ 请提供 source_url 参数（URL、YouTube链接或文件路径）"
        
        args = ["source", "add", source_url, "--json"]
        notebook_id = params.get("notebook_id")
        if notebook_id:
            args.extend(["--notebook", notebook_id])
        
        code, stdout, stderr = await _run_cli(args, user_id, timeout=60)
        if code == 0:
            try:
                data = json.loads(stdout)
                if data.get("error"):
                    return _parse_error(stdout, stderr)
                src_id = data.get("source_id", "Unknown")
                return f"✅ 来源添加成功!\n• ID: `{src_id}`\n• 来源: {source_url}"
            except:
                return f"✅ 来源添加成功:\n```\n{stdout}\n```"
        return _parse_error(stdout, stderr)
    
    if action == "source_list":
        args = ["source", "list", "--json"]
        notebook_id = params.get("notebook_id")
        if notebook_id:
            args.extend(["--notebook", notebook_id])
        
        code, stdout, stderr = await _run_cli(args, user_id)
        if code == 0:
            try:
                data = json.loads(stdout)
                if data.get("error"):
                    return _parse_error(stdout, stderr)
                sources = data.get("sources", [])
                if not sources:
                    return "📄 当前笔记本没有来源。"
                lines = ["📄 **来源列表:**\n"]
                for src in sources:
                    lines.append(f"• **{src.get('title', 'Untitled')}**")
                    lines.append(f"  ID: `{src.get('id')}`")
                    lines.append(f"  类型: {src.get('type', 'Unknown')}")
                return "\n".join(lines)
            except:
                return f"📄 来源列表:\n```\n{stdout}\n```"
        return _parse_error(stdout, stderr)
    
    if action == "source_fulltext":
        source_id = params.get("source_id")
        if not source_id:
            return "❌ 请提供 source_id 参数"
        code, stdout, stderr = await _run_cli(["source", "fulltext", source_id, "--json"], user_id, timeout=60)
        if code == 0:
            try:
                data = json.loads(stdout)
                if data.get("error"):
                    return _parse_error(stdout, stderr)
                text = data.get("fulltext", stdout)
                # 截断过长的文本
                if len(text) > 3000:
                    text = text[:3000] + "\n\n... (文本已截断)"
                return f"📖 **来源全文:**\n\n{text}"
            except:
                return f"📖 来源全文:\n```\n{stdout[:3000]}\n```"
        return _parse_error(stdout, stderr)
    
    if action == "source_guide":
        source_id = params.get("source_id")
        if not source_id:
            return "❌ 请提供 source_id 参数"
        code, stdout, stderr = await _run_cli(["source", "guide", source_id, "--json"], user_id, timeout=60)
        if code == 0:
            try:
                data = json.loads(stdout)
                if data.get("error"):
                    return _parse_error(stdout, stderr)
                guide = data.get("guide", stdout)
                return f"📚 **来源指南:**\n\n{guide}"
            except:
                return f"📚 来源指南:\n```\n{stdout}\n```"
        return _parse_error(stdout, stderr)
    
    # ========== 内容生成 ==========
    if action == "generate_audio":
        # 使用 --no-wait 立即返回，避免长时间等待
        args = ["generate", "audio", "--json", "--no-wait"]
        instructions = params.get("instructions")
        if instructions:
            args.insert(2, instructions)
        
        source_ids = params.get("source_ids", [])
        for sid in source_ids:
            args.extend(["-s", sid])
        
        notebook_id = params.get("notebook_id")
        if notebook_id:
            args.extend(["--notebook", notebook_id])
        
        code, stdout, stderr = await _run_cli(args, user_id, timeout=60)
        if code == 0:
            try:
                data = json.loads(stdout)
                if data.get("error"):
                    return _parse_error(stdout, stderr)
                artifact_id = data.get("artifact_id", "Unknown")
                return (
                    f"🎙️ **播客生成已启动!**\n\n"
                    f"• 内容 ID: `{artifact_id}`\n"
                    f"• 预计耗时: 5-15 分钟\n\n"
                    f"⏰ 请稍后询问我：\"检查播客生成状态\" 或 \"下载播客\""
                )
            except:
                return f"🎙️ 播客生成已启动，请稍后查询状态。\n```\n{stdout}\n```"
        return _parse_error(stdout, stderr)
    
    if action == "generate_video":
        # 使用 --no-wait 立即返回
        args = ["generate", "video", "--json", "--no-wait"]
        instructions = params.get("instructions")
        if instructions:
            args.insert(2, instructions)
        
        notebook_id = params.get("notebook_id")
        if notebook_id:
            args.extend(["--notebook", notebook_id])
        
        code, stdout, stderr = await _run_cli(args, user_id, timeout=60)
        if code == 0:
            try:
                data = json.loads(stdout)
                if data.get("error"):
                    return _parse_error(stdout, stderr)
                artifact_id = data.get("artifact_id", "Unknown")
                return (
                    f"🎬 **视频生成已启动!**\n\n"
                    f"• 内容 ID: `{artifact_id}`\n"
                    f"• 预计耗时: 5-15 分钟\n\n"
                    f"⏰ 请稍后询问我：\"检查视频生成状态\" 或 \"下载视频\""
                )
            except:
                return f"🎬 视频生成已启动，请稍后查询状态。"
        return _parse_error(stdout, stderr)
    
    if action == "generate_quiz":
        args = ["generate", "quiz", "--json"]
        notebook_id = params.get("notebook_id")
        if notebook_id:
            args.extend(["--notebook", notebook_id])
        
        code, stdout, stderr = await _run_cli(args, user_id, timeout=120)
        if code == 0:
            try:
                data = json.loads(stdout)
                if data.get("error"):
                    return _parse_error(stdout, stderr)
                return f"📝 测验生成成功!\n```json\n{json.dumps(data, indent=2, ensure_ascii=False)[:2000]}\n```"
            except:
                return f"📝 测验生成成功:\n```\n{stdout[:2000]}\n```"
        return _parse_error(stdout, stderr)
    
    # ========== 内容管理与下载 ==========
    if action == "artifact_list":
        args = ["artifact", "list", "--json"]
        notebook_id = params.get("notebook_id")
        if notebook_id:
            args.extend(["-n", notebook_id])
        
        code, stdout, stderr = await _run_cli(args, user_id)
        if code == 0:
            try:
                data = json.loads(stdout)
                if data.get("error"):
                    return _parse_error(stdout, stderr)
                artifacts = data.get("artifacts", [])
                if not artifacts:
                    return "📦 没有生成的内容。"
                lines = ["📦 **生成的内容:**\n"]
                for art in artifacts:
                    lines.append(f"• **{art.get('type', 'Unknown')}** - {art.get('status', 'Unknown')}")
                    lines.append(f"  ID: `{art.get('id')}`")
                return "\n".join(lines)
            except:
                return f"📦 内容列表:\n```\n{stdout}\n```"
        return _parse_error(stdout, stderr)
    
    if action == "artifact_wait":
        artifact_id = params.get("artifact_id")
        if not artifact_id:
            return "❌ 请提供 artifact_id 参数"
        
        args = ["artifact", "wait", artifact_id, "--json"]
        notebook_id = params.get("notebook_id")
        if notebook_id:
            args.extend(["-n", notebook_id])
        
        code, stdout, stderr = await _run_cli(args, user_id, timeout=600)
        if code == 0:
            try:
                data = json.loads(stdout)
                if data.get("error"):
                    return _parse_error(stdout, stderr)
                status = data.get("status", "Unknown")
                return f"✅ 内容已完成!\n• 状态: {status}\n\n使用 `download` 操作下载内容。"
            except:
                return f"✅ 内容已完成:\n```\n{stdout}\n```"
        return _parse_error(stdout, stderr)
    
    if action == "download":
        artifact_type = params.get("artifact_type")
        output_path = params.get("output_path")
        
        if not artifact_type:
            return "❌ 请提供 artifact_type 参数 (audio/video/report/mind-map/data-table/quiz/flashcards)"
        
        # 确定输出路径
        if not output_path:
            ext_map = {
                "audio": "mp3", "video": "mp4", "report": "md",
                "mind-map": "json", "data-table": "csv", "quiz": "json", "flashcards": "json"
            }
            ext = ext_map.get(artifact_type, "txt")
            output_path = f"/app/downloads/{user_id}_{artifact_type}.{ext}"
        
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        args = ["download", artifact_type, output_path, "--json"]
        notebook_id = params.get("notebook_id")
        if notebook_id:
            args.extend(["-n", notebook_id])
        artifact_id = params.get("artifact_id")
        if artifact_id:
            args.extend(["-a", artifact_id])
        
        code, stdout, stderr = await _run_cli(args, user_id, timeout=120)
        if code == 0:
            if os.path.exists(output_path):
                # 发送文件给用户
                try:
                    chat_id = update.effective_chat.id
                    file_size = os.path.getsize(output_path)
                    
                    if artifact_type in ["audio", "video"]:
                        # 音频/视频使用对应的发送方法
                        if artifact_type == "audio":
                            await context.bot.send_audio(
                                chat_id=chat_id,
                                audio=open(output_path, "rb"),
                                caption=f"🎙️ NotebookLM 播客\n文件大小: {file_size / 1024 / 1024:.1f}MB"
                            )
                        else:
                            await context.bot.send_video(
                                chat_id=chat_id,
                                video=open(output_path, "rb"),
                                caption=f"🎬 NotebookLM 视频\n文件大小: {file_size / 1024 / 1024:.1f}MB"
                            )
                    else:
                        # 其他文件作为文档发送
                        await context.bot.send_document(
                            chat_id=chat_id,
                            document=open(output_path, "rb"),
                            caption=f"📄 NotebookLM {artifact_type}\n文件大小: {file_size / 1024:.1f}KB"
                        )
                    return f"✅ 文件已发送!"
                except Exception as e:
                    logger.error(f"Failed to send file: {e}")
                    return f"✅ 下载成功!\n• 文件: `{output_path}`\n\n⚠️ 发送失败: {str(e)}"
            return f"✅ 下载完成:\n```\n{stdout}\n```"
        return _parse_error(stdout, stderr)
    
    # ========== 网络研究 ==========
    if action == "research_add":
        query = params.get("research_query")
        if not query:
            return "❌ 请提供 research_query 参数"
        
        args = ["source", "add-research", query, "--json"]
        mode = params.get("research_mode", "fast")
        if mode == "deep":
            args.extend(["--mode", "deep", "--no-wait"])
        
        code, stdout, stderr = await _run_cli(args, user_id, timeout=120)
        if code == 0:
            try:
                data = json.loads(stdout)
                if data.get("error"):
                    return _parse_error(stdout, stderr)
                return f"🔍 网络研究已启动!\n• 查询: {query}\n• 模式: {mode}"
            except:
                return f"🔍 网络研究已启动:\n```\n{stdout}\n```"
        return _parse_error(stdout, stderr)
    
    if action == "research_status":
        code, stdout, stderr = await _run_cli(["research", "status", "--json"], user_id)
        if code == 0:
            return f"🔍 研究状态:\n```\n{stdout}\n```"
        return _parse_error(stdout, stderr)
    
    return f"❌ 未知操作: {action}"
