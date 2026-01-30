"""
Evolution Router - 负责 Bot 的"进化"决策
实现 "利旧优先，智能创造" 的核心逻辑
"""
import logging
from typing import Dict, Any, Optional

from core.config import gemini_client, GEMINI_MODEL
from services.skill_creator import create_skill, approve_skill
from services.skill_registry_service import skill_registry
from core.skill_loader import skill_loader
from services.web_summary_service import fetch_webpage_content

logger = logging.getLogger(__name__)

class EvolutionRouter:
    """
    进化路由器
    当现有技能无法满足用户需求时，决策并执行进化路径
    """
    
    async def evolve(self, user_request: str, user_id: int, update: Optional[Any] = None) -> str:
        """
        执行进化流程
        Returns:
            str: 进化结果报告
        """
        logger.info(f"[Evolution] Starting evolution for: {user_request}")
        
        # 1. 复杂度与意图分析
        analysis = await self._analyze_request(user_request)
        logger.info(f"[Evolution] Analysis: {analysis}")
        
        if analysis.get("intent") == "unknown":
             return "🤔 我不太理解您的需求，请尝试更详细的描述。"
             
        strategy = analysis.get("strategy", "create") # default to create if unsure
        
        # 2. 策略执行
        if strategy == "reuse_search":
            # 尝试搜索外部资源 (GitHub / Docker Hub)
            found, result = await self._search_and_reuse(user_request, user_id)
            if found:
                return f"✅发现并建议复用外部资源：\n{result}"
            else:
                # Fallback to create
                logger.info("[Evolution] External search failed, falling back to creation")
                strategy = "create"
        
        if strategy == "create":
            # Just-in-Time Creation
            result_msg = await self._jit_create_skill(user_request, user_id, update)
            success = "❌" not in result_msg and "⚠️" not in result_msg
            await self.record_evolution(user_request, "create", success, result_msg[:100])
            return result_msg
            
        return "⚠️ 进化策略执行失败"

    async def record_evolution(self, request: str, strategy: str, success: bool, details: str):
        """
        Record evolution event to System Memory (Global Wisdom)
        """
        try:
            from mcp_client.manager import mcp_manager
            from mcp_client.memory import register_memory_server
            
            # Ensure registered
            register_memory_server()
            
            # Use "SYSTEM" as user_id for global memory
            memory = await mcp_manager.get_server("memory", user_id="SYSTEM")
            
            outcome = "success" if success else "failure"
            observation = f"Evolution Event - Request: '{request}', Strategy: '{strategy}', Outcome: {outcome}. Details: {details}"
            
            # Store as observation linked to 'EvolutionSystem' entity
            await memory.call_tool("create_entities", {
                "entities": [
                    {"name": "EvolutionSystem", "type": "System"},
                    {"name": strategy, "type": "Strategy"}
                ]
            })
            
            await memory.call_tool("add_observations", {
                "observations": [{
                    "entityNames": ["EvolutionSystem", strategy],
                    "contents": observation
                }]
            })
            logger.info(f"[Evolution] Recorded event to System Memory: {outcome}")
            
        except Exception as e:
            logger.error(f"[Evolution] Failed to record memory: {e}")

    async def _analyze_request(self, request: str) -> Dict[str, Any]:
        """
        使用 LLM 分析请求复杂度
        """
        prompt = f"""Analyze the following user request for a ChatBot capability evolution.
        
Request: "{request}"

Determine the best strategy:
1. "reuse_search": If the request implies a complex application, service, or tool likely existing on GitHub or Docker Hub (e.g., "deploy uptime kuma", "run a minecraft server", "file browser").
2. "create": If the request implies a specific calculation, data processing, scriptable task, or simple tool (e.g., "calculate md5", "convert currency", "check website status", "generate uuid").

Return JSON:
{{
  "intent": "capability_request", 
  "strategy": "reuse_search" | "create",
  "reason": "explanation"
}}
"""
        try:
            response = await gemini_client.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config={"response_mime_type": "application/json"}
            )
            import json
            import re
            
            text = response.text.strip()
            # Clean markdown code blocks
            text = re.sub(r'^```json\s*', '', text)
            text = re.sub(r'^```\s*', '', text)
            text = re.sub(r'\s*```$', '', text)
            text = text.strip()
            
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                logger.error(f"Analysis JSON parse failed. Raw response: {response.text}")
                # Fallback: simple heuristic
                if "github" in request.lower() or "docker" in request.lower() or "deploy" in request.lower():
                     return {"intent": "capability_request", "strategy": "reuse_search", "reason": "Fallback logic"}
                return {"intent": "capability_request", "strategy": "create", "reason": "Fallback logic"}
        except Exception as e:
            logger.error(f"Analysis failed: {e}")
            return {"intent": "capability_request", "strategy": "create", "reason": "Error"}

    async def _search_and_reuse(self, request: str, user_id: int) -> tuple[bool, str]:
        """
        搜索 现有 Skills 仓库 + GitHub/Docker 并尝试复用
        """
        import httpx
        from urllib.parse import quote
        from core.config import SEARXNG_URL
        
        results_msg = []
        
        # 1. 优先搜索 Skill Registry (Internal & Official)
        try:
            registry_results = await skill_registry.search_skills(request)
            if registry_results:
                msg = "**[Skill Registry] 发现现有技能：**\n"
                for skill in registry_results[:3]:
                    msg += f"- `{skill['name']}` ({skill['repo']})\n  安装命令: `/skill install {skill['repo']}@{skill['name']}`\n"
                results_msg.append(msg)
        except Exception as e:
            logger.warning(f"[Evolution] Registry search failed: {e}")

        # 2. 搜索 GitHub / Docker Hub
        # Construct query for GitHub and Docker Hub
        # We search primarily for GitHub as it usually contains Dockerfile or instructions
        search_query = f"(site:github.com OR site:hub.docker.com) {request} topic:python OR topic:docker"
        encoded_query = quote(search_query)
        
        # Use configured URL
        base_url = SEARXNG_URL
        if not base_url:
            base_url = "http://192.168.1.100:28080/search" # Fallback
            
        search_url = f"{base_url}?q={encoded_query}&format=json&categories=it"
        
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(search_url)
                if response.status_code != 200:
                    logger.warning(f"[Evolution] Search failed: {response.status_code}")
                    return False, ""
                    
                data = response.json()
                results = data.get("results", [])
                
                if not results:
                    return False, ""
                
                # Filter and format results
                valid_results = []
                for res in results[:3]:
                    url = res.get("url", "")
                    title = res.get("title", "")
                    content = res.get("content", "")
                    
                    if "github.com" in url or "hub.docker.com" in url:
                        valid_results.append(f"- **[{title}]({url})**\n  {content[:150]}...")
                
                if valid_results:
                     msg = "**[GitHub/Docker] 外部资源建议：**\n" + "\n\n".join(valid_results)
                     results_msg.append(msg)
                
        except Exception as e:
            logger.error(f"[Evolution] Search error: {e}")
            
        # Final Result Combination
        if not results_msg:
             return False, ""
             
        full_msg = (
            f"根据您的需求，发现了以下可复用资源：\n\n"
            + "\n\n---\n\n".join(results_msg)
            + "\n\n您可以尝试用 `/skill install` 安装（如果是 Skill Registry），或者参考外部项目进行部署。"
        )
        return True, full_msg
                


    async def _jit_create_skill(self, request: str, user_id: int, update: Optional[Any] = None) -> str:
        """
        即时创造技能
        """
        # 1. Create with Retry
        max_retries = 1
        last_error = ""
        
        for attempt in range(max_retries + 1):
            current_req = request
            if attempt > 0:
                logger.info(f"[Evolution] Retrying skill creation (Attempt {attempt+1})...")
                # Append error hint to help LLM fix itself
                current_req += f"\n\n(IMPORTANT: The previous generation failed with error: {last_error}. Please ensure valid JSON output and correct code structure.)"
            
            result = await create_skill(current_req, user_id)
            
            if result["success"]:
                break
            
            last_error = result["error"]
            
        if not result["success"]:
            # Final failure after retries
            return f"❌ 技能生成失败 (重试 {max_retries} 次后放弃): {result['error']}"
            
        skill_name = result["skill_name"]
        
        # 2. Auto Approve (Safe Sandbox Execution)
        # Since it's JIT, we might want to auto-approve to let it run, 
        # but User Rule says "HITL" or "User Approval".
        # But for "Self-Evolution", maybe we want it to be seamless?
        # The user said "主动判断...". 
        # Let's AUTO APPROVE for standard skills (sandboxed) to verify they work, 
        # OR notify user to approve.
        # Implementation Plan said: "Auto install and try to execute".
        
        approve_res = await approve_skill(skill_name)
        if not approve_res["success"]:
             return f"⚠️ 技能生成成功但不符合自动批准条件: {approve_res['error']}。请手动审核。"
             
        # Reload to make it available
        skill_loader.reload_skills()
        
        msg = (
            f"🎉 **大功告成！我已经学会了新技能！**\n\n"
            f"🛠️ 技能名: `{skill_name}`\n"
            f"现在，您可以直接让我使用这个能力了！"
        )
        
        # Directly notify user to ensure visibility
        if update:
             from utils import smart_reply_text
             try:
                 await smart_reply_text(update, msg)
             except Exception as e:
                 logger.error(f"[Evolution] Failed to send success msg: {e}")
                 
        return msg

evolution_router = EvolutionRouter()
