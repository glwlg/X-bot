"""
Skill 路由器 - 支持标准协议和旧版协议
"""
import logging
import json
import re
from typing import Any, Optional, Dict, Tuple

from core.config import gemini_client, ROUTING_MODEL
from .skill_loader import skill_loader

logger = logging.getLogger(__name__)


class SkillRouter:
    """
    智能 Skill 路由器
    
    支持两种 Skill 类型：
    1. 标准协议 (SKILL.md): 通过语义匹配 description
    2. 旧版协议 (.py): 通过 triggers 关键词匹配
    """
    
    def __init__(self):
        self._skills_cache: Optional[list[dict]] = None
    
    def _get_skills_summary(self) -> list[dict]:
        """获取所有 Skill 摘要（带缓存）"""
        if self._skills_cache is None:
            self._skills_cache = skill_loader.get_skills_summary()
        return self._skills_cache
    
    def invalidate_cache(self):
        """清除缓存（添加新 Skill 后调用）"""
        self._skills_cache = None
        skill_loader.reload_skills()
    
    async def route(self, text: str) -> Tuple[Optional[str], Dict[str, Any], str]:
        """
        路由用户消息到 Skill
        
        Returns:
            (skill_name, params, skill_type) 或 (None, {}, "") 表示无匹配
        """
        if not text:
            return None, {}, ""
        
        skills = self._get_skills_summary()
        
        if not skills:
            return None, {}, ""
        
        # 构建路由 prompt
        skills_desc = []
        for s in skills:
            if s.get("skill_type") == "legacy":
                # 旧版 skill 显示 triggers
                skills_desc.append(f"- {s['name']} [LEGACY]: triggers={s.get('triggers', [])}, desc={s.get('description', '')[:50]}")
            else:
                # 标准 skill 只显示 description
                skills_desc.append(f"- {s['name']} [STANDARD]: {s.get('description', '')[:100]}")
        
        skills_str = "\n".join(skills_desc)
        
        routing_prompt = f'''用户消息: "{text}"

可用 Skills:
{skills_str}

任务: 判断用户消息应该由哪个 Skill 处理。

规则:
1. [LEGACY] Skill: 如果消息包含该 Skill 的 triggers 关键词，优先匹配
2. [STANDARD] Skill: 根据 description 语义判断是否匹配用户意图
3. 如果不匹配任何 Skill，返回 null

返回 JSON: {{"skill": "skill_name"}} 或 {{"skill": null}}'''

        try:
            response = gemini_client.models.generate_content(
                model=ROUTING_MODEL,
                contents=routing_prompt,
                config={
                    "response_mime_type": "application/json",
                },
            )
            
            text_response = response.text
            if not text_response:
                return None, {}, ""
            
            # 解析 JSON
            clean_text = re.sub(r"```json|```", "", text_response).strip()
            match = re.search(r"\{.*\}", clean_text, re.DOTALL)
            if match:
                clean_text = match.group(0)
            
            result = json.loads(clean_text)
            skill_name = result.get("skill")
            
            if not skill_name:
                return None, {}, ""
            
            # 获取 skill 信息
            skill_info = skill_loader.get_skill(skill_name)
            if not skill_info:
                logger.warning(f"Routed to unknown skill: {skill_name}")
                return None, {}, ""
            
            skill_type = skill_info.get("skill_type", "standard")
            
            # 对于旧版 skill，提取参数
            if skill_type == "legacy":
                params = await self._extract_legacy_params(skill_name, text, skill_info)
                logger.info(f"Routed to legacy skill: {skill_name} with params: {params}")
                return skill_name, params, "legacy"
            else:
                # 标准 skill 不需要预提取参数，由 SkillExecutor 处理
                logger.info(f"Routed to standard skill: {skill_name}")
                return skill_name, {}, "standard"
            
        except Exception as e:
            logger.error(f"Skill routing error: {e}")
            return None, {}, ""
    
    async def _extract_legacy_params(
        self, 
        skill_name: str, 
        text: str, 
        skill_info: dict
    ) -> Dict[str, Any]:
        """
        为旧版 Skill 提取参数
        """
        params_schema = skill_info.get("params", {})
        
        if not params_schema:
            return {}
        
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
            
            text_response = response.text
            if not text_response:
                return {}
            
            clean_text = re.sub(r"```json|```", "", text_response).strip()
            match = re.search(r"\{.*\}", clean_text, re.DOTALL)
            if match:
                clean_text = match.group(0)
            
            params = json.loads(clean_text)
            
            # 过滤 null 值
            return {k: v for k, v in params.items() if v is not None}
            
        except Exception as e:
            logger.error(f"Param extraction error: {e}")
            return {}


# 全局单例
skill_router = SkillRouter()
