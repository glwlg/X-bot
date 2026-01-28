import asyncio
import logging
import json
from typing import Dict, List, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)

class ContainerService:
    """
    Service to manage running Docker containers and Compose projects.
    """

    async def get_active_services(self) -> str:
        """
        Get a markdown-formatted list of running services.
        Groups containers by Docker Compose project.
        """
        try:
            # Get all containers with specific formatting
            # Format: ID|Names|Status|Label:com.docker.compose.project
            cmd = "docker ps --format '{{.ID}}|{{.Names}}|{{.Status}}|{{.Label \"com.docker.compose.project\"}}'"
            
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                return f"❌ Error listing containers: {stderr.decode()}"
            
            output = stdout.decode().strip()
            if not output:
                return "📭 No active services found."

            projects: Dict[str, List[str]] = {}
            standalone: List[str] = []

            for line in output.splitlines():
                parts = line.split("|")
                if len(parts) < 4: continue
                
                cid, name, status, project = parts
                info = f"- **{name}** ({status})"
                
                if project:
                    if project not in projects:
                        projects[project] = []
                    projects[project].append(info)
                else:
                    standalone.append(info)

            # Build Formatted String
            final_output = ["🐳 **Running Services**\n"]
            
            if projects:
                for proj, items in projects.items():
                    final_output.append(f"📦 **Project: {proj}**")
                    final_output.extend(items)
                    final_output.append("") # Spacer
            
            if standalone:
                final_output.append("📦 **Standalone Containers**")
                final_output.extend(standalone)
            
            if not projects and not standalone:
                return "📭 No active services found."
                
            return "\n".join(final_output)

        except Exception as e:
            logger.error(f"Error getting services: {e}")
            return f"❌ System Error: {str(e)}"

    async def get_networks(self) -> str:
        """
        Get a formatted list of Docker networks.
        """
        try:
            # Format: ID|Name|Driver|Scope
            cmd = "docker network ls --format '{{.ID}}|{{.Name}}|{{.Driver}}|{{.Scope}}'"
            
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                return f"❌ Error listing networks: {stderr.decode()}"
            
            output = stdout.decode().strip()
            if not output:
                return "📭 No networks found."

            # Format as Markdown Table
            lines = ["当前系统中已安装的 Docker 网络列表如下：\n", "| NETWORK ID | NAME | DRIVER | SCOPE |", "| :--- | :--- | :--- | :--- |"]
            
            for row in output.splitlines():
                parts = row.split("|")
                if len(parts) == 4:
                    lines.append(f"| {parts[0]} | {parts[1]} | {parts[2]} | {parts[3]} |")
            
            lines.append("\n目前大部分服务都运行在各自项目的默认网络（如 `project_default`）中。")
            return "\n".join(lines)

        except Exception as e:
            logger.error(f"Error getting networks: {e}")
            return f"❌ System Error: {str(e)}"

    async def stop_service(self, name: str, is_compose_project: bool = False) -> str:
        """
        Safe stop command. 
        If is_compose_project is True, treats 'name' as a project name.
        Otherwise treats 'name' as a container name.
        """
        try:
            if is_compose_project:
                cmd = f"docker compose -p {name} stop"
                msg = f"🛑 Stopping project '{name}'..."
            else:
                cmd = f"docker stop {name}"
                msg = f"🛑 Stopping container '{name}'..."
            
            # Execute
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                return f"✅ Successfully stopped '{name}'."
            else:
                return f"❌ Failed to stop '{name}':\n{stderr.decode()}"

        except Exception as e:
            logger.error(f"Error stopping service {name}: {e}")
            return f"❌ System Error: {str(e)}"

container_service = ContainerService()
