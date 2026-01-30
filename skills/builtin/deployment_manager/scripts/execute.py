import asyncio
import json
import logging
from typing import Dict, Any, List
from core.config import gemini_client, CREATOR_MODEL, GEMINI_MODEL
from google.genai import types
from utils import smart_reply_text, smart_edit_text
import skills.builtin.docker_ops as docker_ops
                from pathlib import Path
                        from repositories.chat_repo import save_message, get_latest_session_id

async def execute(update, context, params):
    goal = params.get("goal")
    url = params.get("repo_url")
    
    manager = DeploymentManager(update, context)
    return await manager.run(goal, url)
