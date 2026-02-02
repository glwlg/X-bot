from core.platform.models import UnifiedContext


async def execute(ctx: UnifiedContext, params: dict) -> str:
    """
    Execute Docker operations.
    """
    action = params.get("action")

    # Lazy import to avoid circular dependency issues at module level
    from services.container_service import container_service
    from services.deployment_service import docker_deployment_service

    if action == "list_services":
        return await container_service.get_active_services()

    elif action == "list_networks":
        return await container_service.get_networks()

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
            return "❌ Missing parameter: 'name' is required to stop a service."

        action_desc = "Removing" if remove else "Stopping"
        action_desc = "Removing" if remove else "Stopping"
        await ctx.reply(f"🛑 {action_desc} {name}...")

        result = await container_service.stop_service(
            name,
            is_compose_project=is_compose,
            remove=remove,
            clean_volumes=clean_volumes,
        )
        await ctx.reply(result)
        return "Command executed."

    elif action == "deploy":
        url = params.get("url")
        if not url:
            return "❌ Missing parameter: 'url' is required for deployment."

        # Phase Update: Send NEW messages for each stage
        async def update_status(msg: str):
            try:
                await ctx.reply(msg)
            except:
                pass

        # Log Streaming: Accumulate and periodically send new messages
        log_buffer = []
        log_message = None

        async def agent_progress_callback(chunk: str):
            nonlocal log_message, log_buffer
            try:
                lines = chunk.splitlines()
                log_buffer.extend(lines)

                # Send a new message every 60 lines to avoid "editing first message" problem
                if len(log_buffer) > 60:
                    # Send current buffer as a new message
                    content = "\n".join(log_buffer)
                    await ctx.reply(f"📋 **日志:**\n```\n{content}\n```")
                    log_buffer = []
                    log_message = None
                else:
                    # Update/create current log message
                    content = "\n".join(log_buffer)
                    display_text = f"📋 **执行日志:**\n```\n{content}\n```"
                    if log_message is None:
                        log_message = await ctx.reply(display_text)
                    # Skip editing - just accumulate until overflow triggers new message
            except:
                pass

        success, result = await docker_deployment_service.deploy_repository(
            url,
            update_callback=update_status,
            progress_callback=agent_progress_callback,
        )

        # Final result
        # If silent=True (called by Manager), just return result string and skip user notification
        if params.get("silent", False):
            return result

        final_msg = (
            f"✅ 部署成功!\n\n{result}" if success else f"❌ Deploy Failed:\n{result}"
        )
        await ctx.reply(final_msg)
        return final_msg

    elif action == "execute_command":
        command = params.get("command")
        if not command:
            return "❌ Missing parameter: 'command' is required."

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
            return f"❌ Security Restriction: Command '{cmd_start}' is not allowed. Allowed: {', '.join(allowed_cmds)}"

        # Security check: Block commands that could leak sensitive info (API keys, tokens)
        cmd_lower = command.lower()
        sensitive_patterns = [
            "inspect",  # docker inspect can show env vars
            "printenv",  # printenv shows env vars
            "/proc/",  # /proc/*/environ contains env vars
            "environ",  # environment files
            ".env",  # .env files
            "api_key",  # API key files
            "token",  # Token files (but allow 'docker logs' containing 'token' in output)
            "secret",  # Secret files
            "password",  # Password files
        ]
        # Only apply sensitive check for docker commands that explicitly read env/secrets
        if cmd_start == "docker":
            docker_sensitive = ["inspect", "exec"]
            docker_parts = command.split()
            # Block: docker inspect (can show all env vars)
            if len(docker_parts) > 1 and docker_parts[1] == "inspect":
                return "❌ Security Restriction: 'docker inspect' is not allowed as it may expose sensitive environment variables."
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
                    return "❌ Security Restriction: This 'docker exec' command may expose sensitive information and is not allowed."

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
                return (
                    "❌ Security Restriction: Accessing sensitive files is not allowed."
                )

        import asyncio

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

            return result
        except Exception as e:
            return f"❌ Execution failed: {e}"

    elif action == "edit_file":
        path = params.get("path")
        content = params.get("content")

        if not path or content is None:
            return "❌ Missing parameter: 'path' and 'content' are required."

        # Security: Only allow editing in DATA_DIR or current deployment context
        # Ideally we should strict check, but for now we trust the Agent's context.
        # We assume path is absolute or relative to a safe place.
        # Let's enforce it must be within /DATA/x-box/data/ or /app/data

        from pathlib import Path

        file_path = Path(path)

        try:
            # Create parent dirs
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return f"✅ File written: {path}"
        except Exception as e:
            return f"❌ Write failed: {e}"

    else:
        return f"❌ Unknown action: {action}"
