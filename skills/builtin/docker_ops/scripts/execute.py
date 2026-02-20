"""
Docker Ops Skill - 容器操作模块

提供 Docker 容器管理的底层操作：
- 列出服务/容器
- 停止/删除容器
- 执行 shell 命令
- 编辑配置文件
- 在指定目录执行 docker compose
"""

import asyncio
import logging
from pathlib import Path

from core.platform.models import UnifiedContext

logger = logging.getLogger(__name__)


async def execute(ctx: UnifiedContext, params: dict, runtime=None):
    """
    Execute Docker operations.
    """
    action = params.get("action")

    # Lazy import to avoid circular dependency
    from services.container_service import container_service

    if action == "list_services":
        res = await container_service.get_active_services()
        yield {"text": res, "ui": {}}
        return

    elif action == "list_networks":
        res = await container_service.get_networks()
        yield {"text": res, "ui": {}}
        return

    # Alias 'remove'/'delete' to 'stop' with cleanup
    elif action in ["remove", "delete"]:
        action = "stop"
        params["remove"] = True
        params["clean_volumes"] = True

    if action == "stop":
        name = params.get("name")
        is_compose = params.get("is_compose", False)
        remove = params.get("remove", False)
        clean_volumes = params.get("clean_volumes", False)

        if not name:
            yield "❌ Missing parameter: 'name' is required to stop a service."
            return

        action_desc = "Removing" if remove else "Stopping"
        yield f"🛑 {action_desc} {name}..."

        result = await container_service.stop_service(
            name,
            is_compose_project=is_compose,
            remove=remove,
            clean_volumes=clean_volumes,
        )
        yield {"text": "Command executed.\n" + result, "ui": {}}
        return

    elif action == "compose_up":
        # 在指定目录执行 docker compose up
        cwd = params.get("cwd") or params.get("path")
        if not cwd:
            yield "❌ Missing parameter: 'cwd' is required for compose_up."
            return

        cwd_path = Path(cwd)
        if not cwd_path.exists():
            yield f"❌ Directory does not exist: {cwd}"
            return

        build = params.get("build", True)
        detach = params.get("detach", True)

        cmd = "docker compose up"
        if build:
            cmd += " --build"
        if detach:
            cmd += " -d"

        yield f"🚀 执行: `{cmd}` in `{cwd}`..."

        try:
            process = await asyncio.create_subprocess_shell(
                cmd,
                cwd=str(cwd_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            output_lines = []
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").rstrip()
                output_lines.append(decoded)

            await process.wait()

            output = "\n".join(output_lines[-50:])  # Last 50 lines

            if process.returncode == 0:
                yield {
                    "text": f"✅ Docker Compose 启动成功!\n\n```\n{output}\n```",
                    "ui": {},
                }
            else:
                yield {
                    "text": f"❌ Docker Compose 启动失败:\n\n```\n{output}\n```",
                    "ui": {},
                }

        except Exception as e:
            yield f"❌ 执行失败: {e}"
        return

    elif action == "compose_down":
        # 在指定目录执行 docker compose down
        cwd = params.get("cwd") or params.get("path")
        if not cwd:
            yield "❌ Missing parameter: 'cwd' is required for compose_down."
            return

        cwd_path = Path(cwd)
        if not cwd_path.exists():
            yield f"❌ Directory does not exist: {cwd}"
            return

        volumes = params.get("volumes", False)
        cmd = "docker compose down"
        if volumes:
            cmd += " -v"

        yield f"🛑 执行: `{cmd}` in `{cwd}`..."

        try:
            process = await asyncio.create_subprocess_shell(
                cmd,
                cwd=str(cwd_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            output = stdout.decode() + stderr.decode()

            if process.returncode == 0:
                yield {
                    "text": f"✅ Docker Compose 已停止\n\n```\n{output}\n```",
                    "ui": {},
                }
            else:
                yield {
                    "text": f"❌ Docker Compose 停止失败:\n\n```\n{output}\n```",
                    "ui": {},
                }

        except Exception as e:
            yield f"❌ 执行失败: {e}"
        return

    elif action == "execute_command":
        command = params.get("command")
        cwd = params.get("cwd")  # Optional working directory

        if not command:
            yield "❌ Missing parameter: 'command' is required."
            return

        # Security check: Allow specific safe commands
        cmd_start = command.strip().split()[0]
        allowed_cmds = [
            "docker",
            "curl",
            "netstat",
            "ss",
            "grep",
            "cat",
            "ls",
            "pwd",
            "sed",
            "awk",
            "head",
            "tail",
        ]
        if cmd_start not in allowed_cmds:
            yield f"❌ Security Restriction: Command '{cmd_start}' is not allowed. Allowed: {', '.join(allowed_cmds)}"
            return

        # Security check: Block commands that could leak sensitive info
        cmd_lower = command.lower()
        if cmd_start == "docker":
            docker_parts = command.split()
            # Block: docker inspect (can show all env vars)
            if len(docker_parts) > 1 and docker_parts[1] == "inspect":
                yield "❌ Security Restriction: 'docker inspect' is not allowed as it may expose sensitive environment variables."
                return
            # Block: docker exec with env-reading commands
            if len(docker_parts) > 2 and docker_parts[1] == "exec":
                exec_cmd = " ".join(docker_parts[3:]) if len(docker_parts) > 3 else ""
                if any(
                    p in exec_cmd.lower()
                    for p in [
                        "env",
                        "printenv",
                        "environ",
                        ".env",
                        "secret",
                        "password",
                        "token",
                        "api_key",
                    ]
                ):
                    yield "❌ Security Restriction: This 'docker exec' command may expose sensitive information and is not allowed."
                    return

        # Block cat/grep on sensitive files
        if cmd_start in ["cat", "grep"]:
            if any(
                p in cmd_lower
                for p in [
                    ".env",
                    "secret",
                    "password",
                    "api_key",
                    "token",
                    "/proc/",
                    "environ",
                ]
            ):
                yield "❌ Security Restriction: Accessing sensitive files is not allowed."
                return

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            output = stdout.decode().strip()
            error = stderr.decode().strip()

            result = ""
            if output:
                result += f"{output}\n"
            if error:
                result += f"Stderr: {error}\n"

            if not result:
                result = "Command executed successfully (no output)."

            yield result
            return
        except Exception as e:
            yield f"❌ Execution failed: {e}"
            return

    elif action == "edit_file":
        path = params.get("path")
        content = params.get("content")

        if not path or content is None:
            yield "❌ Missing parameter: 'path' and 'content' are required."
            return

        file_path = Path(path)

        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            yield f"✅ File written: {path}"
        except Exception as e:
            yield f"❌ Write failed: {e}"
        return

    else:
        yield {"text": f"❌ Unknown action: {action}", "ui": {}}
        return
