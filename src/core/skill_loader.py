"""
Skill 加载器 - 支持标准 SKILL.md 协议和旧版 .py 格式
"""
import os
import ast
import logging
import importlib.util
import yaml
from typing import Any, Optional, Dict, List
from pathlib import Path
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class StandardSkill:
    """标准 SKILL.md 协议的 Skill"""
    name: str
    description: str
    skill_md_path: str
    skill_md_content: str
    scripts: List[str] = field(default_factory=list)
    source: str = "learned"
    skill_type: str = "standard"  # "standard" or "legacy"
    
    # 额外元数据
    license: str = ""
    

@dataclass 
class LegacySkill:
    """旧版 .py 格式的 Skill"""
    name: str
    description: str
    triggers: List[str]
    path: str
    source: str = "builtin"
    skill_type: str = "legacy"


class SkillLoader:
    """Skill 动态加载器 - 支持双协议"""
    
    def __init__(self, skills_dir: str = None):
        if skills_dir is None:
            # 自动探测 skills 目录
            base_dir = os.path.dirname(__file__)
            docker_path = os.path.join(base_dir, "..", "skills")
            local_path = os.path.join(base_dir, "..", "..", "skills")
            
            if os.path.exists(local_path) and os.path.isdir(local_path):
                skills_dir = local_path
            else:
                skills_dir = docker_path
        
        self.skills_dir = os.path.abspath(skills_dir)
        logger.info(f"Using skills directory: {self.skills_dir}")
        
        # 已加载的旧版 Skill 模块缓存
        self._loaded_modules: Dict[str, Any] = {}
        
        # Skill 索引（统一格式）
        self._skill_index: Dict[str, Dict] = {}
        
    def scan_skills(self) -> Dict[str, Dict]:
        """
        扫描所有 Skill 目录，支持两种格式：
        1. 标准协议: 目录包含 SKILL.md
        2. 旧版协议: .py 文件包含 SKILL_META
        """
        self._skill_index.clear()
        
        for subdir in ["builtin", "learned"]:
            dir_path = os.path.join(self.skills_dir, subdir)
            if not os.path.exists(dir_path):
                continue
            
            for entry in os.listdir(dir_path):
                entry_path = os.path.join(dir_path, entry)
                
                # 检查是否为标准协议目录（包含 SKILL.md）
                if os.path.isdir(entry_path):
                    skill_md_path = os.path.join(entry_path, "SKILL.md")
                    if os.path.exists(skill_md_path):
                        skill_info = self._parse_standard_skill(skill_md_path, entry_path, subdir)
                        if skill_info:
                            self._skill_index[skill_info["name"]] = skill_info
                            logger.info(f"Indexed standard skill: {skill_info['name']} from {subdir}")
                        continue
                
                # 检查是否为旧版 .py 文件
                if entry.endswith(".py") and not entry.startswith("_"):
                    skill_info = self._parse_legacy_skill(entry_path, subdir)
                    if skill_info:
                        self._skill_index[skill_info["name"]] = skill_info
                        logger.info(f"Indexed legacy skill: {skill_info['name']} from {subdir}")
        
        return self._skill_index
    
    def _parse_standard_skill(self, skill_md_path: str, skill_dir: str, source: str) -> Optional[Dict]:
        """
        解析标准 SKILL.md 格式
        
        格式:
        ---
        name: skill_name
        description: Short description
        license: MIT
        ---
        
        # Markdown content...
        """
        try:
            with open(skill_md_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            # 解析 YAML frontmatter
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    frontmatter = yaml.safe_load(parts[1])
                    markdown_content = parts[2].strip()
                else:
                    logger.warning(f"Invalid SKILL.md format in {skill_md_path}")
                    return None
            else:
                # 没有 frontmatter，使用目录名作为 name
                frontmatter = {"name": os.path.basename(skill_dir)}
                markdown_content = content
            
            name = frontmatter.get("name", os.path.basename(skill_dir))
            description = frontmatter.get("description", "")
            
            # 扫描 scripts 目录
            scripts = []
            scripts_dir = os.path.join(skill_dir, "scripts")
            if os.path.exists(scripts_dir):
                for script in os.listdir(scripts_dir):
                    if script.endswith(".py"):
                        scripts.append(script)
            
            return {
                "name": name,
                "description": description,
                "skill_type": "standard",
                "skill_md_path": skill_md_path,
                "skill_md_content": markdown_content,
                "skill_dir": skill_dir,
                "scripts": scripts,
                "source": source,
                "license": frontmatter.get("license", ""),
            }
            
        except Exception as e:
            logger.error(f"Error parsing standard skill {skill_md_path}: {e}")
            return None
    
    def _parse_legacy_skill(self, filepath: str, source: str) -> Optional[Dict]:
        """
        解析旧版 .py 格式（包含 SKILL_META）
        """
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                file_source = f.read()
            
            tree = ast.parse(file_source)
            
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == "SKILL_META":
                            try:
                                meta = ast.literal_eval(node.value)
                                # 验证必要字段
                                if "name" not in meta or "triggers" not in meta:
                                    continue
                                    
                                return {
                                    "name": meta.get("name"),
                                    "description": meta.get("description", ""),
                                    "skill_type": "legacy",
                                    "triggers": meta.get("triggers", []),
                                    "params": meta.get("params", {}),
                                    "path": filepath,
                                    "source": source,
                                }
                            except (ValueError, TypeError) as e:
                                logger.warning(f"Cannot parse SKILL_META in {filepath}: {e}")
            
            return None
            
        except Exception as e:
            logger.error(f"Error parsing legacy skill {filepath}: {e}")
            return None
    
    def get_skill_index(self) -> Dict[str, Dict]:
        """获取当前索引"""
        if not self._skill_index:
            self.scan_skills()
        return self._skill_index
    
    def get_skills_summary(self) -> List[Dict]:
        """
        获取所有 Skill 的摘要（用于 AI 路由）
        """
        index = self.get_skill_index()
        summary = []
        
        for name, info in index.items():
            skill_summary = {
                "name": name,
                "description": info.get("description", "")[:200],
                "skill_type": info.get("skill_type"),
            }
            
            # 旧版 skill 有 triggers
            if info.get("skill_type") == "legacy":
                skill_summary["triggers"] = info.get("triggers", [])
            
            summary.append(skill_summary)
        
        return summary
    
    def get_skill(self, skill_name: str) -> Optional[Dict]:
        """获取 Skill 完整信息"""
        index = self.get_skill_index()
        return index.get(skill_name)
    
    def load_legacy_skill(self, skill_name: str) -> Optional[Any]:
        """
        加载旧版 .py Skill 模块（用于执行）
        """
        # 检查缓存
        if skill_name in self._loaded_modules:
            return self._loaded_modules[skill_name]
        
        # 查找索引
        index = self.get_skill_index()
        if skill_name not in index:
            logger.warning(f"Skill not found: {skill_name}")
            return None
        
        skill_info = index[skill_name]
        
        # 只能加载旧版 skill
        if skill_info.get("skill_type") != "legacy":
            logger.warning(f"Skill {skill_name} is not a legacy skill, cannot load as module")
            return None
        
        filepath = skill_info["path"]
        
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
            logger.info(f"Loaded legacy skill: {skill_name}")
            return module
            
        except Exception as e:
            logger.error(f"Error loading legacy skill {skill_name}: {e}")
            return None
    
    def reload_skills(self):
        """重新扫描所有 Skills"""
        self._loaded_modules.clear()
        self.scan_skills()
    
    def unload_skill(self, skill_name: str) -> bool:
        """卸载 Skill"""
        if skill_name in self._loaded_modules:
            del self._loaded_modules[skill_name]
            logger.info(f"Unloaded skill: {skill_name}")
            return True
        return False


# 全局单例
skill_loader = SkillLoader()
