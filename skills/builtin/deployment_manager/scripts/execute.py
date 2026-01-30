import asyncio
import json
import logging
from typing import Dict, Any, List
from pathlib import Path

# Internal imports
from core.config import gemini_client, CREATOR_MODEL, GEMINI_MODEL
from google.genai import types
from utils import smart_reply_text, smart_edit_text

# Import docker_ops execution logic
# We import the 'execute' function from the docker_ops script
from skills.builtin.docker_ops.scripts.execute import execute as docker_ops_execute

logger = logging.getLogger(__name__)

# Use CREATOR_MODEL (Pro) for better reasoning, fallback to GEMINI_MODEL
MANAGER_MODEL = CREATOR_MODEL or GEMINI_MODEL

SYSTEM_PROMPT = """
You are a Senior DevOps Manager Agent. Your goal is to autonomously deploy applications using Docker.
You have access to an Executor (Tools). You must plan, execute, verify, and correct until the goal is met.

## GLOBAL CONSTRAINTS (MUST FOLLOW):
1. **Ports**: All exposed ports MUST be > 20000. If a service defaults to 80/8080, you MUST map it to 20000+ (e.g., 20080:80).
2. **Health**: The service must return HTTP 200 (or acceptable status) on the verified port.
3. **Persistence**: Ensure data volumes are mapped correctly.
4. **NO REDUNDANT DEPLOY**: If the previous step 'deploy' was successful, DO NOT deploy again immediately. Proceed to 'execute' commands to verify ports and health. Only deploy again if you changed configuration.
5. **PRIORITIZE DOCKER-COMPOSE**: Always prefer deploying via `docker-compose.yml` if available.
6. **NO BUILD**: DO NOT build images from Dockerfile unless there is NO ready-made image available on Docker Hub/GitHub Container Registry.
7. **FULL URL OUTPUT**: When the task is finished, you MUST output the **Exact Access URL** (e.g., http://192.168.1.100:20080). DO NOT use placeholders like `<host>`, `localhost`, or `127.0.0.1` unless running locally. Read the logs to find the correct IP.

## TOOLS AVAILABLE:
1. `deploy(url)`: Clone and deploy a git repo. Returns logs and initial ports.
2. `execute(command)`: Run shell command (docker logs, docker ps, curl, netstat, ls, cat).
3. `edit(path, content)`: Overwrite a file (usually docker-compose.yml).
4. `stop(name)`: Stop a container/project.
5. `read_file(path)`: Read file content.
6. `finish(success, message)`: End the task.

## RESPONSE FORMAT:
You must output a JSON object describing your THOUGHT and NEXT ACTION.
**IMPORTANT**: The content of the `thought` field MUST be in **Chinese** (Simplified Chinese), so the user can understand your reasoning.
{
  "thought": "我需要先检查...",
  "action": "execute",
  "params": {"command": "curl ..."}
}
"""

class DeploymentManager:
    def __init__(self, update, context):
        self.update = update
        self.context = context
        self.history = []
        self.max_loops = 20

    async def run(self, goal: str, repo_url: str = None):
        # Initial context
        context_str = f"Goal: {goal}\nRepo URL: {repo_url or 'Unknown'}"
        self.history.append({"role": "user", "parts": [context_str]})
        
        for i in range(self.max_loops):
            # 1. Plan
            # response = await self._think()
            # Temporary fix for "google.genai.types.GenerateContentConfig" error if types isn't imported correctly or API changes
            # But here we use standard usage.
            response = await self._think()

            if not response:
                await smart_reply_text(self.update, "❌ 思考中断，请重试。")
                break
                
            thought = response.get("thought", "")
            action = response.get("action", "")
            params = response.get("params", {})
            
            # User feedback
            if action != "finish":
                await smart_reply_text(self.update, f"🤖 {thought}")
            
            # 2. Execute
            result = await self._execute_tool(action, params)
            
            # 3. Observe
            obs = f"Observation from {action}: {result}"
            self.history.append({"role": "user", "parts": [obs]})
            
            # Check finish
            if action == "finish":
                return result

        return "❌ Max loops reached without success."

    async def _think(self) -> Dict:
        try:
            # Build conversation string
            conversation = f"system: {SYSTEM_PROMPT}\n"
            for item in self.history:
                conversation += f"{item['role']}: {item['parts'][0]}\n"
            conversation += "model: (Produce JSON)\n"

            response = await gemini_client.aio.models.generate_content(
                model=MANAGER_MODEL,
                contents=conversation,
                config={"response_mime_type": "application/json"}
            )
            
            text = response.text
            if not text: return None
                
            text = text.strip()
            # Remove markdown code blocks
            if text.startswith("```json"): text = text[7:]
            elif text.startswith("```"): text = text[3:]
            if text.endswith("```"): text = text[:-3]
            
            return json.loads(text.strip())
        except Exception as e:
            logger.error(f"Manager thinking error: {e}")
            return None

    async def _execute_tool(self, action: str, params: Dict) -> str:
        try:
            if action == "deploy":
                url = params.get("url")
                # Call docker_ops.execute
                return await docker_ops_execute(self.update, self.context, {"action": "deploy", "url": url, "silent": True})
            
            elif action == "execute":
                cmd = params.get("command")
                return await docker_ops_execute(self.update, self.context, {"action": "execute_command", "command": cmd})
            
            elif action == "edit":
                path = params.get("path")
                content = params.get("content")
                return await docker_ops_execute(self.update, self.context, {"action": "edit_file", "path": path, "content": content})

            elif action == "stop":
                name = params.get("name")
                return await docker_ops_execute(self.update, self.context, {"action": "stop", "name": name, "is_compose": True})
            
            elif action == "read_file":
                path = params.get("path")
                p = Path(path)
                if p.exists():
                    return p.read_text(encoding='utf-8')[:5000]
                else:
                    return "File not found."

            elif action == "finish":
                msg = params.get("message", "Done.")
                final_text = f"🏁 **任务完成**: {msg}"
                await smart_reply_text(self.update, final_text)
                return ""

            else:
                return f"Unknown action: {action}"
        except Exception as e:
            return f"Tool execution failed: {e}"

async def execute(update, context, params):
    goal = params.get("goal")
    url = params.get("repo_url")
    
    manager = DeploymentManager(update, context)
    return await manager.run(goal, url)
