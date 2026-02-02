from core.platform.models import UnifiedContext
import asyncio


async def execute(ctx: UnifiedContext, params: dict):
    """
    Execute Docker operations.
    """
    action = params.get("action")

    # Lazy import to avoid circular dependency issues at module level
    from services.container_service import container_service
    from services.deployment_service import docker_deployment_service

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
        # Mutate params to use 'stop' logic
        action = "stop"
        params["remove"] = True
        params["clean_volumes"] = True  # Default to clean volumes for explicit delete

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

    elif action == "deploy":
        url = params.get("url")
        if not url:
            yield "❌ Missing parameter: 'url' is required for deployment."
            return

        # Queue for streaming logs
        log_queue = asyncio.Queue()

        # Phase Update
        async def update_status(msg: str):
            await log_queue.put(f"🚀 {msg}")

        # Log Streaming
        log_buffer = []

        async def agent_progress_callback(chunk: str):
            nonlocal log_buffer
            try:
                lines = chunk.splitlines()
                log_buffer.extend(lines)

                # Send a new message every 60 lines
                if len(log_buffer) > 60:
                    content = "\n".join(log_buffer)
                    await log_queue.put(f"📋 **日志:**\n```\n{content}\n```")
                    log_buffer = []
            except Exception:
                pass

        # Run deployment in background task
        deploy_task = asyncio.create_task(
            docker_deployment_service.deploy_repository(
                url,
                update_callback=update_status,
                progress_callback=agent_progress_callback,
            )
        )

        # Consume queue while task runs
        while not deploy_task.done():
            try:
                # Wait for next log or task completion (polling with short timeout)
                # Using wait might be better but timeout is simple for now
                msg = await asyncio.wait_for(log_queue.get(), timeout=0.5)
                yield msg
            except asyncio.TimeoutError:
                continue

        # Drain remaining logs
        while not log_queue.empty():
            msg = await log_queue.get()
            yield msg

        # Flush buffer
        if log_buffer:
            content = "\n".join(log_buffer)
            yield f"📋 **日志:**\n```\n{content}\n```"

        success, result = await deploy_task

        # Final result
        if params.get("silent", False):
            yield result
            return

        final_msg = (
            f"✅ 部署成功!\n\n{result}" if success else f"❌ Deploy Failed:\n{result}"
        )
        yield {"text": final_msg, "ui": {}}
        return

    elif action == "execute_command":
        command = params.get("command")
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
        ]
        if cmd_start not in allowed_cmds:
            yield f"❌ Security Restriction: Command '{cmd_start}' is not allowed. Allowed: {', '.join(allowed_cmds)}"
            return

        # Security check: Block commands that could leak sensitive info (API keys, tokens)
        cmd_lower = command.lower()
        # Only apply sensitive check for docker commands that explicitly read env/secrets
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
                command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
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

        from pathlib import Path

        file_path = Path(path)

        try:
            # Create parent dirs
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            yield f"✅ File written: {path}"
        except Exception as e:
            yield f"❌ Write failed: {e}"
        return

    else:
        yield {"text": f"❌ Unknown action: {action}", "ui": {}}
        return
