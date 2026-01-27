SKILL_META = {
    "name": "docker_ops",
    "description": "Manage Docker containers, services, and networks. Use this skill to list running services, check networks, stop containers, or deploy new repositories.",
    "triggers": [
        "deploy_github_repo",
        "list_containers",
        "stop_container",
        "list_networks"
    ],
    "params": {
        "action": "Action to perform: 'list_services', 'list_networks', 'stop', 'deploy'",
        "url": "GitHub URL for deployment (required for 'deploy')",
        "name": "Container or project name (required for 'stop')",
        "is_compose": "Boolean, true if stopping a compose project"
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
    from utils import smart_reply_text, smart_edit_text
    
    if action == "list_services":
        return await container_service.get_active_services()
        
    elif action == "list_networks":
        return await container_service.get_networks()
        
    elif action == "stop":
        name = params.get("name")
        is_compose = params.get("is_compose", False)
        if not name:
            return "❌ Missing parameter: 'name' is required to stop a service."
        
        status_msg = await smart_reply_text(update, f"🛑 Stopping {name}...")
        result = await container_service.stop_service(name, is_compose_project=is_compose)
        await smart_edit_text(status_msg, result)
        return "Command executed."

    elif action == "deploy":
        url = params.get("url")
        if not url:
            return "❌ Missing parameter: 'url' is required for deployment."
            
        status_msg = await smart_reply_text(update, f"🚀 Deploying {url}...")
        
        async def update_status(msg: str):
            try:
                await smart_edit_text(status_msg, msg)
            except:
                pass

        async def agent_progress_callback(msg: str):
            pass

        success, result = await docker_deployment_service.deploy_repository(
            url, 
            update_callback=update_status,
            progress_callback=agent_progress_callback
        )
        
        final_msg = result if success else f"Deploy Failed: {result}"
        await update_status(final_msg)
        return "Deployment finished."

    else:
        return f"❌ Unknown action: {action}"
