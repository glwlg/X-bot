"""
Deployment Manager Skill - 基础操作模块

提供部署相关的基础文件操作，供 Skill Agent 调度使用。
Agent 通过 SKILL.md 中定义的 SOP 编排 searxng_search、web_browser、docker_ops 完成部署。
"""

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path

from core.config import (
    X_DEPLOYMENT_STAGING_PATH,
    is_user_allowed,
    SERVER_IP,
)
from core.platform.models import UnifiedContext

logger = logging.getLogger(__name__)

# 服务器地址 - 用于构建访问 URL
DISPLAY_IP = SERVER_IP or "localhost"

# 工作目录 - 必须是宿主机绝对路径
if not X_DEPLOYMENT_STAGING_PATH:
    logger.warning(
        "⚠️ X_DEPLOYMENT_STAGING_PATH 未配置！部署功能可能无法正常工作。"
        "请在 .env 中设置为宿主机绝对路径。"
    )
    WORK_BASE = Path("/tmp/deployment_staging")  # Fallback, 不推荐
else:
    WORK_BASE = Path(X_DEPLOYMENT_STAGING_PATH)

WORK_BASE.mkdir(parents=True, exist_ok=True)


async def execute(ctx: UnifiedContext, params: dict):
    """
    执行部署管理器的基础操作。

    可用 action:
    - clone: 克隆 GitHub 仓库
    - write_file: 创建/编辑文件
    - read_file: 读取文件
    - list_dir: 列出目录
    - status: 查看已部署项目
    - get_access_info: 获取项目访问信息
    """
    action = params.get("action", "status")

    if action == "clone":
        return await _clone_repo(params)

    elif action == "write_file":
        return await _write_file(params)

    elif action == "read_file":
        return await _read_file(params)

    elif action == "list_dir":
        return await _list_dir(params)

    elif action == "status":
        return await _get_status()

    elif action == "delete_project":
        return await _delete_project(params)

    elif action == "get_access_info":
        return await _get_access_info(params)

    elif action == "verify_access":
        return await _verify_access(params)

    else:
        return {
            "text": f"❌ 未知操作: {action}。支持: clone, write_file, read_file, list_dir, status, get_access_info, verify_access",
            "ui": {},
        }


async def _clone_repo(params: dict) -> dict:
    """克隆 GitHub 仓库"""
    repo_url = params.get("repo_url", "")
    if not repo_url:
        return {"text": "❌ 缺少参数: repo_url", "ui": {}}

    # 解析项目名
    repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
    target_dir = params.get("target_dir") or str(WORK_BASE / repo_name)
    target_path = Path(target_dir)

    try:
        if target_path.exists():
            # 更新已有仓库
            logger.info(f"Updating existing repository: {target_path}")
            subprocess.run(
                ["git", "reset", "--hard", "HEAD"],
                cwd=str(target_path),
                check=False,
                capture_output=True,
            )
            process = await asyncio.create_subprocess_exec(
                "git",
                "pull",
                "--rebase",
                cwd=str(target_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                # 更新失败，尝试重新克隆
                shutil.rmtree(target_path, ignore_errors=True)
                return await _do_clone(repo_url, target_path)

            return {
                "text": f"✅ 仓库已更新: {repo_name}\n\n路径: `{target_path}`",
                "ui": {},
                "project_path": str(target_path),
                "project_name": repo_name,
            }
        else:
            return await _do_clone(repo_url, target_path)

    except Exception as e:
        logger.error(f"Clone error: {e}")
        return {"text": f"❌ 克隆失败: {e}", "ui": {}}


async def _do_clone(repo_url: str, target_path: Path) -> dict:
    """执行 git clone"""
    repo_name = target_path.name

    process = await asyncio.create_subprocess_exec(
        "git",
        "clone",
        "--depth",
        "1",
        repo_url,
        str(target_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    if process.returncode == 0:
        return {
            "text": f"✅ 仓库克隆成功: {repo_name}\n\n路径: `{target_path}`",
            "ui": {},
            "project_path": str(target_path),
            "project_name": repo_name,
        }
    else:
        error_msg = stderr.decode("utf-8", errors="replace")
        return {"text": f"❌ 克隆失败:\n```\n{error_msg}\n```", "ui": {}}


async def _write_file(params: dict) -> dict:
    """创建或编辑文件"""
    path = params.get("path", "")
    content = params.get("content", "")

    if not path:
        return {"text": "❌ 缺少参数: path", "ui": {}}

    file_path = Path(path)

    try:
        # 确保父目录存在
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # 写入文件
        file_path.write_text(content, encoding="utf-8")

        return {
            "text": f"✅ 文件已写入: `{file_path}`\n\n内容长度: {len(content)} 字符",
            "ui": {},
            "file_path": str(file_path),
        }
    except Exception as e:
        logger.error(f"Write file error: {e}")
        return {"text": f"❌ 写入失败: {e}", "ui": {}}


async def _read_file(params: dict) -> dict:
    """读取文件内容"""
    path = params.get("path", "")

    if not path:
        return {"text": "❌ 缺少参数: path", "ui": {}}

    file_path = Path(path)

    if not file_path.exists():
        return {"text": f"❌ 文件不存在: `{file_path}`", "ui": {}}

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        # 截断过长的内容
        if len(content) > 10000:
            content = content[:10000] + "\n\n... (内容过长，已截断)"

        return {
            "text": f"📄 文件内容 (`{file_path}`):\n\n```\n{content}\n```",
            "ui": {},
            "content": content,
        }
    except Exception as e:
        logger.error(f"Read file error: {e}")
        return {"text": f"❌ 读取失败: {e}", "ui": {}}


async def _list_dir(params: dict) -> dict:
    """列出目录内容"""
    path = params.get("path", str(WORK_BASE))
    dir_path = Path(path)

    if not dir_path.exists():
        return {"text": f"❌ 目录不存在: `{dir_path}`", "ui": {}}

    try:
        items = []
        for item in sorted(dir_path.iterdir()):
            if item.is_dir():
                items.append(f"📁 {item.name}/")
            else:
                size = item.stat().st_size
                items.append(f"📄 {item.name} ({size} bytes)")

        if not items:
            return {"text": f"📂 目录为空: `{dir_path}`", "ui": {}}

        return {
            "text": f"📂 目录内容 (`{dir_path}`):\n\n" + "\n".join(items),
            "ui": {},
            "items": [str(p) for p in dir_path.iterdir()],
        }
    except Exception as e:
        logger.error(f"List dir error: {e}")
        return {"text": f"❌ 列出目录失败: {e}", "ui": {}}


async def _get_status() -> dict:
    """获取已部署项目状态"""
    projects = []

    try:
        # 列出工作目录下的所有项目
        for item in WORK_BASE.iterdir():
            if item.is_dir():
                # 检查是否有 docker-compose.yml
                compose_file = item / "docker-compose.yml"
                if not compose_file.exists():
                    compose_file = item / "docker-compose.yaml"

                has_compose = compose_file.exists()
                projects.append(
                    {
                        "name": item.name,
                        "path": str(item),
                        "has_compose": has_compose,
                    }
                )

        if not projects:
            return {
                "text": "📭 暂无部署项目。\n\n工作目录: `" + str(WORK_BASE) + "`",
                "ui": {},
            }

        # 获取运行中的容器及其端口
        container_ports = {}  # {container_name: [ports]}
        try:
            process = await asyncio.create_subprocess_shell(
                "docker ps --format '{{.Names}}|{{.Ports}}'",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await process.communicate()
            for line in stdout.decode().strip().split("\n"):
                if "|" in line:
                    name, ports_str = line.split("|", 1)
                    # 解析端口，如 "0.0.0.0:21000->3001/tcp"
                    ports = []
                    import re

                    for match in re.findall(r"0\.0\.0\.0:(\d+)->", ports_str):
                        ports.append(int(match))
                    container_ports[name] = ports
        except Exception:
            pass

        # 构建输出
        lines = ["📋 **已部署项目**:\n"]
        for proj in projects:
            name = proj["name"]

            # 查找匹配的容器
            matching_ports = []
            for container_name, ports in container_ports.items():
                if name in container_name:
                    matching_ports.extend(ports)

            if matching_ports:
                status = "🟢 运行中"
                urls = [f"http://{DISPLAY_IP}:{p}" for p in sorted(set(matching_ports))]
                access_info = " | ".join(urls)
                lines.append(f"• **{name}**: {status}")
                lines.append(f"  📍 访问: {access_info}")
            else:
                status = "⚪ 未运行"
                compose_status = (
                    "✓ docker-compose" if proj["has_compose"] else "✗ 无配置"
                )
                lines.append(f"• **{name}**: {status} ({compose_status})")

        lines.append(f"\n工作目录: `{WORK_BASE}`")

        return {"text": "\n".join(lines), "ui": {}}

    except Exception as e:
        logger.error(f"Get status error: {e}")
        return {"text": f"❌ 获取状态失败: {e}", "ui": {}}


async def _get_access_info(params: dict) -> dict:
    """获取特定项目的访问信息"""
    import re

    name = params.get("name", "")
    if not name:
        return {"text": "❌ 缺少参数: name", "ui": {}}

    try:
        # 查询 docker ps 获取端口信息
        process = await asyncio.create_subprocess_shell(
            "docker ps --format '{{.Names}}|{{.Ports}}'",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()

        ports = []
        for line in stdout.decode().strip().split("\n"):
            if "|" in line:
                container_name, ports_str = line.split("|", 1)
                if name in container_name:
                    # 解析端口
                    for match in re.findall(r"0\.0\.0\.0:(\d+)->", ports_str):
                        ports.append(int(match))

        if not ports:
            return {
                "text": f"⚠️ 未找到运行中的容器: {name}\n\n请先确保服务已启动。",
                "ui": {},
            }

        urls = [f"http://{DISPLAY_IP}:{p}" for p in sorted(set(ports))]

        result = f"✅ **{name}** 访问信息:\n\n"
        for url in urls:
            result += f"📍 {url}\n"

        return {"text": result, "ui": {}, "urls": urls}

    except Exception as e:
        logger.error(f"Get access info error: {e}")
        return {"text": f"❌ 获取访问信息失败: {e}", "ui": {}}


async def _verify_access(params: dict) -> dict:
    """
    验证部署的服务是否可访问。

    使用 httpx 检查 URL 是否可达。
    如果不可达，返回诊断信息供 AI 继续处理。
    """
    import httpx

    name = params.get("name", "")
    url = params.get("url", "")
    timeout = params.get("timeout", 10)  # 默认 10 秒超时

    # 如果没有提供 URL，尝试从容器获取
    if not url and name:
        access_result = await _get_access_info({"name": name})
        urls = access_result.get("urls", [])
        if urls:
            url = urls[0]  # 使用第一个端口

    if not url:
        return {
            "text": "❌ 缺少参数: 需要 `url` 或 `name` 来确定检查目标。",
            "ui": {},
            "success": False,
        }

    # 确保 URL 有协议前缀
    if not url.startswith("http"):
        url = f"http://{url}"

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url)

            if response.status_code < 400:
                return {
                    "text": f"✅ **服务验证成功!**\n\n"
                    f"📍 访问地址: {url}\n"
                    f"📊 状态码: {response.status_code}\n"
                    f"📄 响应长度: {len(response.content)} bytes",
                    "ui": {},
                    "success": True,
                    "url": url,
                    "status_code": response.status_code,
                }
            else:
                return {
                    "text": f"⚠️ **服务响应异常**\n\n"
                    f"📍 URL: {url}\n"
                    f"📊 状态码: {response.status_code}\n\n"
                    f"服务可能需要更多时间初始化，或配置有误。",
                    "ui": {},
                    "success": False,
                    "url": url,
                    "status_code": response.status_code,
                }

    except httpx.ConnectError:
        # 连接失败 - 可能服务未启动
        diag = await _get_container_diagnostics(name) if name else ""
        return {
            "text": f"❌ **连接失败**: 无法连接到 {url}\n\n"
            f"**可能原因**:\n"
            f"• 服务尚未完全启动（需要等待）\n"
            f"• 端口映射配置错误\n"
            f"• 容器内服务崩溃\n\n"
            f"{diag}",
            "ui": {},
            "success": False,
            "error": "connect_error",
            "url": url,
        }

    except httpx.TimeoutException:
        return {
            "text": f"⏰ **连接超时**: {url} 在 {timeout} 秒内无响应\n\n"
            f"**建议**:\n"
            f"• 等待几秒后重试\n"
            f"• 检查容器日志",
            "ui": {},
            "success": False,
            "error": "timeout",
            "url": url,
        }

    except Exception as e:
        logger.error(f"Verify access error: {e}")
        return {
            "text": f"❌ **验证失败**: {e}",
            "ui": {},
            "success": False,
            "error": str(e),
        }


async def _get_container_diagnostics(name: str) -> str:
    """获取容器诊断信息"""
    try:
        # 检查容器是否在运行
        process = await asyncio.create_subprocess_shell(
            f"docker ps -a --filter 'name={name}' --format '{{{{.Names}}}}|{{{{.Status}}}}|{{{{.Ports}}}}'",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        output = stdout.decode().strip()

        if not output:
            return f"**诊断**: 未找到名称包含 `{name}` 的容器。\n请检查是否已执行 `docker compose up`。"

        lines = []
        for line in output.split("\n"):
            if "|" in line:
                parts = line.split("|")
                container_name = parts[0]
                status = parts[1] if len(parts) > 1 else "Unknown"
                ports = parts[2] if len(parts) > 2 else "None"
                lines.append(f"• `{container_name}`: {status}")
                if ports:
                    lines.append(f"  端口: {ports}")

        # 获取最近日志
        log_process = await asyncio.create_subprocess_shell(
            f"docker logs --tail 5 $(docker ps -q --filter 'name={name}' | head -1) 2>&1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        log_stdout, _ = await log_process.communicate()
        recent_logs = log_stdout.decode().strip()

        result = "**容器状态**:\n" + "\n".join(lines)
        if recent_logs:
            result += f"\n\n**最近日志**:\n```\n{recent_logs[:500]}\n```"

        return result

    except Exception as e:
        return f"**诊断失败**: {e}"


async def _delete_project(params: dict) -> dict:
    """删除部署项目"""
    name = params.get("name", "")
    if not name:
        return {"text": "❌ 缺少参数: name", "ui": {}}

    project_path = WORK_BASE / name

    if not project_path.exists():
        return {"text": f"❌ 项目不存在: {name}", "ui": {}}

    try:
        shutil.rmtree(project_path)
        return {"text": f"✅ 项目已删除: {name}", "ui": {}}
    except Exception as e:
        logger.error(f"Delete project error: {e}")
        return {"text": f"❌ 删除失败: {e}", "ui": {}}


# =============================================================================
# Handler Registration (for /deploy command)
# =============================================================================
def register_handlers(adapter_manager):
    """注册 /deploy 命令"""

    async def deploy_command(ctx: UnifiedContext):
        """
        Handle /deploy <描述或URL>
        这是入口命令，实际部署逻辑由 Skill Agent 通过 SKILL.md SOP 编排
        """
        if not await is_user_allowed(ctx.message.user.id):
            return

        args = ctx.platform_ctx.args if ctx.platform_ctx else []
        if not args:
            await ctx.reply(
                "⚠️ 请提供部署目标。\n\n"
                "用法:\n"
                "• `/deploy https://github.com/user/repo` - 部署 GitHub 项目\n"
                "• `/deploy Uptime Kuma` - 智能搜索并部署"
            )
            return

        # 将请求转发给 Agent 处理
        from core.agent_orchestrator import agent_orchestrator

        user_input = " ".join(args)
        full_request = f"部署: {user_input}"

        await ctx.reply(f"🚀 收到部署请求: {user_input}\n\n正在分析...")

        # 调用 Agent 处理
        async for response in agent_orchestrator.handle_message(
            ctx=ctx,
            user_input=full_request,
            attachments=[],
        ):
            if response and response.strip():
                await ctx.reply(response)

    adapter_manager.on_command("deploy", deploy_command, description="智能部署服务")
