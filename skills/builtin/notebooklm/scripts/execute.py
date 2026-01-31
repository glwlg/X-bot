import asyncio
import json
import os
from core.platform.models import UnifiedContext


async def execute(ctx: UnifiedContext, params: dict) -> str:
    """执行 NotebookLM 操作"""
    action = params.get("action", "").lower()
    user_id = int(ctx.message.user.id) if str(ctx.message.user.id).isdigit() else 0

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
        code, stdout, stderr = await _run_cli(
            ["notebook", "delete", notebook_id, "--json"], user_id
        )
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
        code, stdout, stderr = await _run_cli(
            ["source", "fulltext", source_id, "--json"], user_id, timeout=60
        )
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
        code, stdout, stderr = await _run_cli(
            ["source", "guide", source_id, "--json"], user_id, timeout=60
        )
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
                    f'⏰ 请稍后询问我："检查播客生成状态" 或 "下载播客"'
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
                    f'⏰ 请稍后询问我："检查视频生成状态" 或 "下载视频"'
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
                    lines.append(
                        f"• **{art.get('type', 'Unknown')}** - {art.get('status', 'Unknown')}"
                    )
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
                "audio": "mp3",
                "video": "mp4",
                "report": "md",
                "mind-map": "json",
                "data-table": "csv",
                "quiz": "json",
                "flashcards": "json",
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
                    file_size = os.path.getsize(output_path)

                    if artifact_type in ["audio", "video"]:
                        # 音频/视频使用对应的发送方法
                        if artifact_type == "audio":
                            await ctx.reply_audio(
                                audio=open(output_path, "rb"),
                                caption=f"🎙️ NotebookLM 播客\n文件大小: {file_size / 1024 / 1024:.1f}MB",
                            )
                        else:
                            await ctx.reply_video(
                                video=open(output_path, "rb"),
                                caption=f"🎬 NotebookLM 视频\n文件大小: {file_size / 1024 / 1024:.1f}MB",
                            )
                    else:
                        # 其他文件作为文档发送
                        await ctx.reply_document(
                            document=open(output_path, "rb"),
                            caption=f"📄 NotebookLM {artifact_type}\n文件大小: {file_size / 1024:.1f}KB",
                        )
                    return f"✅ 文件已发送!"
                except Exception as e:
                    logger.error(f"Failed to send file: {e}")
                    return (
                        f"✅ 下载成功!\n• 文件: `{output_path}`\n\n⚠️ 发送失败: {str(e)}"
                    )
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


async def _run_cli(args: list, user_id: int, timeout: int = 30):
    """运行 notebooklm CLI 命令"""
    # 直接运行 notebooklm，无需 uv run
    cmd = ["notebooklm"] + args

    env = os.environ.copy()

    # 复制用户认证文件到默认位置 (~/.notebooklm/storage_state.json)
    # 以支持多用户切换
    try:
        user_auth_src = f"/app/data/users/{user_id}/notebooklm/storage_state.json"

        home_dir = os.path.expanduser("~")
        target_dir = os.path.join(home_dir, ".notebooklm")
        target_auth_dst = os.path.join(target_dir, "storage_state.json")

        if os.path.exists(user_auth_src):
            os.makedirs(target_dir, exist_ok=True)
            import shutil

            shutil.copy2(user_auth_src, target_auth_dst)
    except Exception as e:
        return -1, "", f"Auth file error: {e}"
    # 可以在这里为不同用户设置不同的配置路径，例如:
    # env["NOTEBOOKLM_STORAGE_PATH"] = f"/app/data/users/{user_id}/notebooklm.json"

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            try:
                process.kill()
            except:
                pass
            return -1, "", "Execution timed out"

        return process.returncode, stdout.decode().strip(), stderr.decode().strip()
    except Exception as e:
        return -1, "", str(e)


def _parse_error(stdout, stderr):
    """解析错误输出"""
    err_msg = stderr if stderr else stdout
    # 尝试提取简洁的错误信息
    if "Error:" in err_msg:
        err_msg = err_msg.split("Error:", 1)[1].strip()
    return f"❌ 操作失败: {err_msg}"


async def _find_notebook_id(user_id, title):
    """通过标题查找笔记本 ID"""
    code, stdout, stderr = await _run_cli(["list", "--json"], user_id)
    if code != 0:
        return None
    try:
        data = json.loads(stdout)
        for nb in data.get("notebooks", []):
            if nb.get("title") == title:
                return nb.get("id")
    except:
        pass
    return None
