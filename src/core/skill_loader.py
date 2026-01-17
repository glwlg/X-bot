"""
Skill 加载器 - 支持热加载和按需加载
"""
import os
import ast
import logging
import importlib.util
from typing import Any, Optional
from pathlib import Path

from .skill_base import SkillMeta, validate_skill_meta

logger = logging.getLogger(__name__)


class SkillLoader:
    """Skill 动态加载器"""
    
    def __init__(self, skills_dir: str = None):
        if skills_dir is None:
            # 默认在 src/skills 目录
            skills_dir = os.path.join(os.path.dirname(__file__), "..", "skills")
        self.skills_dir = os.path.abspath(skills_dir)
        
        # 已加载的 Skill 模块缓存
        self._loaded_modules: dict[str, Any] = {}
        
        # Skill 索引（只包含元数据，不加载代码）
        self._skill_index: dict[str, dict] = {}
        
    def scan_skills(self) -> dict[str, dict]:
        """
        扫描所有 Skill 目录，提取元数据建立索引
        不执行模块代码，只解析 SKILL_META
        """
        self._skill_index.clear()
        
        for subdir in ["builtin", "learned"]:
            dir_path = os.path.join(self.skills_dir, subdir)
            if not os.path.exists(dir_path):
                continue
                
            for filename in os.listdir(dir_path):
                if not filename.endswith(".py") or filename.startswith("_"):
                    continue
                    
                filepath = os.path.join(dir_path, filename)
                meta = self._extract_meta_from_file(filepath)
                
                if meta:
                    skill_name = meta.get("name", filename[:-3])
                    self._skill_index[skill_name] = {
                        "meta": meta,
                        "path": filepath,
                        "source": subdir,
                    }
                    logger.info(f"Indexed skill: {skill_name} from {subdir}")
        
        return self._skill_index
    
    def _extract_meta_from_file(self, filepath: str) -> Optional[dict]:
        """
        从文件中提取 SKILL_META，使用 AST 解析避免执行代码
        """
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                source = f.read()
            
            tree = ast.parse(source)
            
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == "SKILL_META":
                            # 安全地评估字典字面量
                            try:
                                meta = ast.literal_eval(node.value)
                                valid, msg = validate_skill_meta(meta)
                                if valid:
                                    return meta
                                else:
                                    logger.warning(f"Invalid skill meta in {filepath}: {msg}")
                            except (ValueError, TypeError) as e:
                                logger.warning(f"Cannot parse SKILL_META in {filepath}: {e}")
            
            return None
            
        except Exception as e:
            logger.error(f"Error extracting meta from {filepath}: {e}")
            return None
    
    def get_skill_index(self) -> dict[str, dict]:
        """获取当前索引"""
        if not self._skill_index:
            self.scan_skills()
        return self._skill_index
    
    def get_triggers_summary(self) -> list[dict]:
        """
        获取所有 Skill 的触发词摘要（用于快速路由）
        返回轻量级数据，不包含完整元数据
        """
        index = self.get_skill_index()
        return [
            {
                "name": name,
                "triggers": info["meta"]["triggers"],
                "description": info["meta"]["description"][:100],  # 截断
            }
            for name, info in index.items()
        ]
    
    def load_skill(self, skill_name: str) -> Optional[Any]:
        """
        按需加载单个 Skill 模块
        """
        # 检查缓存
        if skill_name in self._loaded_modules:
            return self._loaded_modules[skill_name]
        
        # 查找索引
        index = self.get_skill_index()
        if skill_name not in index:
            logger.warning(f"Skill not found: {skill_name}")
            return None
        
        filepath = index[skill_name]["path"]
        
        try:
            spec = importlib.util.spec_from_file_location(skill_name, filepath)
            if spec is None or spec.loader is None:
                logger.error(f"Cannot create module spec for {skill_name}")
                return None
            
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # 验证模块结构
            if not hasattr(module, "SKILL_META") or not hasattr(module, "execute"):
                logger.error(f"Skill {skill_name} missing SKILL_META or execute function")
                return None
            
            self._loaded_modules[skill_name] = module
            logger.info(f"Loaded skill: {skill_name}")
            return module
            
        except Exception as e:
            logger.error(f"Error loading skill {skill_name}: {e}")
            return None
    
    def reload_skill(self, skill_name: str) -> Optional[Any]:
        """热重载 Skill"""
        # 清除缓存
        self._loaded_modules.pop(skill_name, None)
        
        # 重新扫描索引（可能是新添加的）
        self.scan_skills()
        
        return self.load_skill(skill_name)
    
    def unload_skill(self, skill_name: str) -> bool:
        """卸载 Skill"""
        if skill_name in self._loaded_modules:
            del self._loaded_modules[skill_name]
            logger.info(f"Unloaded skill: {skill_name}")
            return True
        return False


# 全局单例
skill_loader = SkillLoader()
