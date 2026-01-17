"""
Skill 路由器 - 两阶段路由，按需加载
"""
import logging
from typing import Any, Optional

from core.config import gemini_client, ROUTING_MODEL
from .skill_loader import skill_loader

logger = logging.getLogger(__name__)


class SkillRouter:
    """
    两阶段 Skill 路由器
    
    Phase 1: 快速匹配 - 使用关键词 + 轻量 LLM 调用
    Phase 2: 精确路由 - 加载匹配 Skill，提取参数
    """
    
    def __init__(self):
        self._triggers_cache: Optional[list[dict]] = None
    
    def _get_triggers(self) -> list[dict]:
        """获取触发词摘要（带缓存）"""
        if self._triggers_cache is None:
            self._triggers_cache = skill_loader.get_triggers_summary()
        return self._triggers_cache
    
    def invalidate_cache(self):
        """清除缓存（添加新 Skill 后调用）"""
        self._triggers_cache = None
        skill_loader.scan_skills()
    
    async def route(self, text: str) -> tuple[Optional[str], dict[str, Any]]:
        """
        路由用户消息到 Skill
        
        Returns:
            (skill_name, params) 或 (None, {}) 表示无匹配
        """
        if not text:
            return None, {}
        
        triggers = self._get_triggers()
        
        if not triggers:
            return None, {}
        
        # Phase 1: 快速匹配
        # 构建轻量 prompt，只包含 name + triggers
        skills_desc = "\n".join([
            f"- {s['name']}: triggers={s['triggers']}"
            for s in triggers
        ])
        
        routing_prompt = f'''用户消息: "{text}"

可用 Skills:
{skills_desc}

任务: 判断用户消息匹配哪个 Skill。

规则:
1. 如果消息明确匹配某个 Skill 的触发词或意图，返回该 Skill
2. 如果不匹配任何 Skill，返回 null

返回 JSON 格式: {{"skill": "skill_name"}} 或 {{"skill": null}}'''

        try:
            response = gemini_client.models.generate_content(
                model=ROUTING_MODEL,
                contents=routing_prompt,
                config={
                    "response_mime_type": "application/json",
                },
            )
            
            import json
            import re
            
            text_response = response.text
            if not text_response:
                return None, {}
            
            # 清理并解析
            clean_text = re.sub(r"```json|```", "", text_response).strip()
            match = re.search(r"\{.*\}", clean_text, re.DOTALL)
            if match:
                clean_text = match.group(0)
            
            result = json.loads(clean_text)
            skill_name = result.get("skill")
            
            if not skill_name:
                return None, {}
            
            # Phase 2: 加载 Skill 并提取参数
            return await self._extract_params(skill_name, text)
            
        except Exception as e:
            logger.error(f"Skill routing error: {e}")
            return None, {}
    
    async def _extract_params(self, skill_name: str, text: str) -> tuple[str, dict[str, Any]]:
        """
        Phase 2: 加载 Skill 并提取参数
        """
        module = skill_loader.load_skill(skill_name)
        
        if not module:
            return None, {}
        
        meta = module.SKILL_META
        params_schema = meta.get("params", {})
        
        if not params_schema:
            # 无参数 Skill
            return skill_name, {}
        
        # 构建参数提取 prompt
        params_desc = "\n".join([
            f"- {name}: {info.get('description', info.get('type', 'str'))}"
            for name, info in params_schema.items()
        ])
        
        extract_prompt = f'''从用户消息中提取参数。

用户消息: "{text}"

需要提取的参数:
{params_desc}

返回 JSON 格式: {{"param_name": "value", ...}}
如果某个参数无法提取，使用 null。'''

        try:
            response = gemini_client.models.generate_content(
                model=ROUTING_MODEL,
                contents=extract_prompt,
                config={
                    "response_mime_type": "application/json",
                },
            )
            
            import json
            import re
            
            text_response = response.text
            if not text_response:
                return skill_name, {}
            
            clean_text = re.sub(r"```json|```", "", text_response).strip()
            match = re.search(r"\{.*\}", clean_text, re.DOTALL)
            if match:
                clean_text = match.group(0)
            
            params = json.loads(clean_text)
            
            # 过滤 null 值
            params = {k: v for k, v in params.items() if v is not None}
            
            logger.info(f"Routed to skill: {skill_name} with params: {params}")
            return skill_name, params
            
        except Exception as e:
            logger.error(f"Param extraction error: {e}")
            return skill_name, {}


# 全局单例
skill_router = SkillRouter()
