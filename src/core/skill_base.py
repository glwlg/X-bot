"""
Skill 基础协议定义

每个 Skill 都是一个 Python 模块，需要包含：
1. SKILL_META - 元数据字典
2. execute() - 异步执行函数
"""
from typing import TypedDict, Protocol, Any, Optional
from telegram import Update
from telegram.ext import ContextTypes


class SkillParams(TypedDict, total=False):
    """Skill 参数定义"""
    type: str  # str, int, bool
    optional: bool
    enum: list[str]  # 可选枚举值
    description: str


class SkillMeta(TypedDict):
    """Skill 元数据"""
    name: str  # 唯一标识
    description: str  # 功能描述
    triggers: list[str]  # 触发关键词
    params: dict[str, SkillParams]  # 参数定义
    version: str  # 版本号
    author: str  # 作者（user_id 或 'system'）


class SkillProtocol(Protocol):
    """Skill 模块协议"""
    SKILL_META: SkillMeta
    
    async def execute(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        params: dict[str, Any]
    ) -> None:
        """执行 Skill 逻辑"""
        ...


# 内置 Skill 作者标识
SYSTEM_AUTHOR = "system"


def validate_skill_meta(meta: dict) -> tuple[bool, str]:
    """验证 Skill 元数据完整性"""
    required_fields = ["name", "description", "triggers", "params"]
    
    for field in required_fields:
        if field not in meta:
            return False, f"Missing required field: {field}"
    
    if not isinstance(meta["triggers"], list) or len(meta["triggers"]) == 0:
        return False, "triggers must be a non-empty list"
    
    if not isinstance(meta["params"], dict):
        return False, "params must be a dict"
    
    return True, "OK"
