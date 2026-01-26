import logging
import json
import re
from typing import Optional, Dict, Any, Tuple

from core.config import gemini_client, ROUTING_MODEL
from services.skill_registry_service import skill_registry
from .skill_router import skill_router
from .skill_loader import skill_loader
from services.intent_router import analyze_intent, UserIntent

logger = logging.getLogger(__name__)

class AutonomicRouter:
    """
    智能自主路由器
    
    层级：
    1. 本地 Skill (Local Skills) - 最优先，用户自定义或已安装的特定能力
    2. 原生 Intent (Native Intents) - 系统内置核心能力 (下载、提醒等)
    3. 外部 Skill 发现 (Discovery) - 当上述都无法处理时，尝试搜索外部市场
    """
    
    async def route(self, text: str) -> Tuple[str, Dict[str, Any], str]:
        """
        Returns:
            (route_type, result, message)
            
            route_type: "skill", "skill_standard", "intent", "discovery_wait", "none"
            result: 
              - for skill: {"skill_name": "...", "params": ..., "skill_type": ...}
              - for intent: {"intent": "...", "params": ...}
              - for discovery: {"query": "..."}
        """
        if not text:
            return "none", {}, ""

        # 1. 尝试本地 Skill
        skill_name, skill_params, skill_type = await skill_router.route(text)
        if skill_name:
            if skill_type == "standard":
                # 标准协议 Skill
                return "skill_standard", {
                    "skill_name": skill_name, 
                    "params": skill_params,
                    "skill_type": "standard"
                }, f"📚 匹配到标准技能：{skill_name}"
            else:
                # 旧版 Skill
                return "skill_legacy", {
                    "skill_name": skill_name, 
                    "params": skill_params,
                    "skill_type": "legacy"
                }, f"🔮 匹配到技能：{skill_name}"
            
        # 2. 尝试原生 Intent
        intent_result = await analyze_intent(text)
        intent = intent_result.get("intent")
        
        if intent != UserIntent.GENERAL_CHAT and intent != UserIntent.UNKNOWN:
            return "intent", intent_result, f"🎯 识别到意图：{intent.value}"
            
        # 3. 技能发现 (Skill Discovery)
        discovery_result = await self._check_discovery_need(text)
        if discovery_result:
            return "discovery_wait", discovery_result, "🔍 正在搜索新技能..."
            
        # 4. 兜底：普通对话
        return "intent", {"intent": UserIntent.GENERAL_CHAT, "params": {}}, ""

    async def _check_discovery_need(self, text: str) -> Optional[Dict]:
        """
        判断是否需要搜索外部技能
        """
        prompt = f"""User Message: "{text}"
        
        Current Capabilities:
        - Download Video/Audio
        - Download Video/Audio
        - Set Reminder
        - Set Reminder
        - RSS Subscribe/Monitor
        - Stock Watch
        - General Chat
        
        Task: Determine if the user is asking for a specific FUNCTION that is NOT in the current capabilities, but could likely be solved by installing a software tool or plugin (Skill).
        
        - "Check weather in Tokyo" -> YES (need weather tool)
        - "Calculate md5 of string" -> YES (need utility)
        - "Tell me a joke" -> NO (general chat)
        - "Translate this" -> NO (general chat/native)
        - "Download this video" -> NO (native)
        
        If YES, return JSON: {{"need_search": true, "query": "search keywords"}}
        If NO, return JSON: {{"need_search": false}}
        """
        
        try:
            response = gemini_client.models.generate_content(
                model=ROUTING_MODEL,
                contents=prompt,
                config={"response_mime_type": "application/json"}
            )
            
            import json
            res = json.loads(response.text)
            if res.get("need_search"):
                return {"query": res.get("query")}
            return None
            
        except Exception:
            return None

    async def perform_discovery_and_install(self, query: str, update_callback=None) -> Tuple[bool, str]:
        """
        执行搜索和安装流程 - Fail-Fast 策略
        """
        if update_callback:
            await update_callback(f"🔍 正在技能市场搜索 '{query}'...")
            
        skills = await skill_registry.search_skills(query)
        
        if not skills:
            # 记录为 Feature Request
            await self._record_feature_request(query)
            return False, "未找到相关技能，已记录为功能需求。"
        
        # 智能排序：名称完全匹配 > 名称包含关键词 > 其他
        query_lower = query.lower().replace(" ", "-")
        
        # 分组
        exact_match = []
        partial_match = []
        others = []
        
        for skill in skills:
            s_name = skill["name"].lower()
            if s_name == query_lower:
                exact_match.append(skill)
            elif query_lower in s_name or s_name in query_lower:
                partial_match.append(skill)
            else:
                others.append(skill)
        
        # 候选列表 (只取前 3 个以免耗时过长)
        candidates = (exact_match + partial_match + others)[:3]
        
        import shutil
        import os
        
        for i, candidate in enumerate(candidates):
            skill_name = candidate["name"]
            repo = candidate["repo"]
            
            if update_callback:
                await update_callback(f"⬇️ 尝试安装候选 [{i+1}/{len(candidates)}]: {skill_name} ({repo})...")
            
            # 1. 安装
            success = await skill_registry.install_skill(repo, skill_name)
            
            if not success:
                logger.warning(f"Install failed for {skill_name}, trying next...")
                continue
            
            # 2. 重新扫描并验证
            skill_loader.scan_skills()
            
            # 3. 验证是否有效加载 (Fail-Fast)
            skill_info = skill_loader.get_skill(skill_name)
            
            if skill_info:
                # 再次确认是否真的能解析 (虽然 get_skill 应该是已经解析过的)
                # 如果解析过程中有错，skill_loader log 会显示，但 get_skill 可能返回 None
                # 这里 skill_info 非空说明解析成功
                if update_callback:
                    await update_callback(f"✅ 技能 '{skill_name}' 验证通过！")
                
                skill_router.invalidate_cache()
                return True, skill_name
            else:
                # 安装了但没加载到（说明解析失败，例如 YAML 错误）
                logger.error(f"Skill {skill_name} installed but failed verification (parsing error). Uninstalling...")
                
                if update_callback:
                    await update_callback(f"⚠️ 技能 '{skill_name}' 格式无效，正在移除并重试...")
                
                # 4. 立即卸载 (清理垃圾)
                # 假设安装在 skills/learned/{skill_name} 或 skills/learned/{skill_name}.py
                learned_dir = os.path.join(skill_loader.skills_dir, "learned")
                
                # Try directory
                dir_path = os.path.join(learned_dir, skill_name)
                if os.path.exists(dir_path) and os.path.isdir(dir_path):
                    shutil.rmtree(dir_path)
                
                # Try file
                file_path = os.path.join(learned_dir, f"{skill_name}.py")
                if os.path.exists(file_path):
                    os.remove(file_path)
                    
                # Continue loop to next candidate
        
        # 所有候选都失败
        await self._record_feature_request(query)
        return False, f"尝试了 {len(candidates)} 个技能均无法通过验证，已记录为 Feature Request。"

    async def _record_feature_request(self, query: str):
        """记录 Feature Request"""
        try:
            import datetime
            from core.config import DATA_DIR
            
            req_dir = os.path.join(DATA_DIR, "feature_requests")
            if not os.path.exists(req_dir):
                os.makedirs(req_dir, exist_ok=True)
            
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"auto_req_{timestamp}.md"
            filepath = os.path.join(req_dir, filename)
            
            content = f"""# Feature Request (Auto-Generated)

**Query**: {query}
**Time**: {datetime.datetime.now()}
**Source**: AutonomicRouter (Skill Discovery Failure)

## Description
User requested functionality that could not be satisfied by local skills or discovered external skills.
"""
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            
            logger.info(f"Recorded feature request: {filepath}")
            
        except Exception as e:
            logger.error(f"Error recording feature request: {e}")

autonomic_router = AutonomicRouter()
