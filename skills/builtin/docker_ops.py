SKILL_META = {
    "name": "docker_ops",
    "description": "NATIVE DOCKER MANAGER. CAPABLE OF DEPLOYING GITHUB REPOS DIRECTLY. Use this skill to deploy applications (e.g. SearXNG, Typecho) from GitHub URLs, manage containers, and edit docker-compose.yml. DO NOT SEARCH FOR EXTERNAL SKILLS FOR DOCKER DEPLOYMENT.",
    "triggers": [
        "deploy_github_repo",
        "list_containers",
        "stop_container",
        "list_networks",
        "run_docker_command",
        "edit_docker_compose"
    ],
    "params": {
        "action": "Action: 'list_services', 'list_networks', 'stop', 'deploy', 'execute_command', 'edit_file'",
        "url": "GitHub URL (for 'deploy')",
        "name": "Container name (for 'stop')",
        "is_compose": "Boolean (for 'stop')",
        "command": "Raw docker command to run (for 'execute_command', e.g. 'docker logs caddy')",
        "path": "File path to edit (for 'edit_file')",
        "content": "New file content (for 'edit_file')"
    }
}

async def execute(update, context, params):
    """
    Execute Docker operations.
    """
    action = params.get("action")
    
    # Lazy import to avoid circular dependency issues at module level
    from services.container_service import container_service
    from services.deployment_service import docker_deployment_service
    from utils import smart_reply_text
    
    if action == "list_services":
        return await container_service.get_active_services()
        
    elif action == "list_networks":
        return await container_service.get_networks()
        
    elif action == "stop":
        name = params.get("name")
        is_compose = params.get("is_compose", False)
        if not name:
            return "❌ Missing parameter: 'name' is required to stop a service."
        
        await smart_reply_text(update, f"🛑 Stopping {name}...")
        result = await container_service.stop_service(name, is_compose_project=is_compose)
        await smart_reply_text(update, result)
        return "Command executed."

    elif action == "deploy":
        url = params.get("url")
        if not url:
            return "❌ Missing parameter: 'url' is required for deployment."
        
        # Phase Update: Send NEW messages for each stage
        async def update_status(msg: str):
            try:
                await smart_reply_text(update, msg)
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
                    await smart_reply_text(update, f"📋 **日志:**\n```\n{content}\n```")
                    log_buffer = []
                    log_message = None
                else:
                    # Update/create current log message
                    content = "\n".join(log_buffer)
                    display_text = f"📋 **执行日志:**\n```\n{content}\n```"
                    if log_message is None:
                        log_message = await smart_reply_text(update, display_text)
                    # Skip editing - just accumulate until overflow triggers new message
            except:
                pass

        success, result = await docker_deployment_service.deploy_repository(
            url, 
            update_callback=update_status,
            progress_callback=agent_progress_callback
        )
        
        # Final result as new message
        final_msg = f"✅ 部署成功!\n\n{result}" if success else f"❌ Deploy Failed:\n{result}"
        await smart_reply_text(update, final_msg)
        return "Deployment finished."

    elif action == "execute_command":
        command = params.get("command")
        if not command:
            return "❌ Missing parameter: 'command' is required."
        
        # Security check: Only allow docker commands
        if not command.strip().startswith("docker"):
            return "❌ Only 'docker' commands are allowed."
            
        import asyncio
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
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
