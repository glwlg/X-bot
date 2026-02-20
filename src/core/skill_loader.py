"""
Skill 加载器 - 支持标准 SKILL.md 协议和旧版 .py 格式
"""

import os
import logging
import yaml
from typing import Any, Optional, Dict, List

logger = logging.getLogger(__name__)


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
        扫描所有 Skill 目录,只支持标准 SKILL.md 格式
        """
        self._skill_index.clear()

        for subdir in ["builtin", "learned"]:
            dir_path = os.path.join(self.skills_dir, subdir)
            if not os.path.exists(dir_path):
                continue

            for entry in os.listdir(dir_path):
                entry_path = os.path.join(dir_path, entry)

                # 只检查目录
                if os.path.isdir(entry_path):
                    skill_md_path = os.path.join(entry_path, "SKILL.md")
                    if os.path.exists(skill_md_path):
                        skill_info = self._parse_standard_skill(
                            skill_md_path, entry_path, subdir
                        )
                        if skill_info:
                            self._skill_index[skill_info["name"]] = skill_info
                            logger.info(
                                f"Indexed standard skill: {skill_info['name']} from {subdir}"
                            )
        logger.info(
            f"Total skills indexed: {len(self._skill_index)}. Keys: {list(self._skill_index.keys())}"
        )

        return self._skill_index

    def _parse_standard_skill(
        self, skill_md_path: str, skill_dir: str, source: str
    ) -> Optional[Dict]:
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
            triggers = frontmatter.get("triggers", [])  # 解析触发词
            cron_instruction = frontmatter.get("cron_instruction")  # 解析 Cron 任务指令
            params = frontmatter.get("params", {})

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
                "triggers": triggers,  # 添加 triggers
                "cron_instruction": cron_instruction,  # 任务指令
                "params": params,  # 参数定义
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
                "description": info.get("description", "")[:500],
                "triggers": info.get("triggers", []),  # 添加 triggers
            }

            summary.append(skill_summary)

        return summary

    async def find_similar_skills(
        self, query: str, threshold: float = 0.4
    ) -> List[Dict]:
        """
        查找相似技能 (AI Semantic Search)
        使用 LLM 语义匹配，支持本地兜底
        """
        from core.config import gemini_client, ROUTING_MODEL
        import json
        import difflib

        skills = self.get_skills_summary()
        if not skills:
            return []

        # 1. 快速检查：精准名称匹配 (Exact Match) - save Token
        query_lower = query.lower()
        for skill in skills:
            if skill["name"].lower() == query_lower:
                skill["score"] = 1.0
                return [skill]

        # 2. AI Semantic Search
        try:
            # 构建 Prompt
            skills_context = "\n".join(
                [
                    f"- name: {s['name']}\n  description: {s.get('description', '')}"
                    for s in skills
                ]
            )

            prompt = f"""
You are a smart Skill Matcher.
Identify which skills from the list might match the user's intent.
Refuse to match if the relevance is low.

User Query: "{query}"

Available Skills:
{skills_context}

Return JSON:
{{
  "matches": [
    {{ "name": "skill_name", "score": 0.0_to_1.0, "reason": "brief explanation" }}
  ]
}}
"""

            response = await gemini_client.aio.models.generate_content(
                model=ROUTING_MODEL,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )

            if not response.text:
                raise ValueError("Empty response from AI")

            try:
                data = json.loads(response.text)
                matches_data = data.get("matches", [])
            except json.JSONDecodeError:
                text = response.text.replace("```json", "").replace("```", "").strip()
                data = json.loads(text)
                matches_data = data.get("matches", [])

            matched_skills = []
            skill_map = {s["name"]: s for s in skills}

            for m in matches_data:
                name = m.get("name")
                score = m.get("score", 0.0)
                if name in skill_map and score >= threshold:
                    s = skill_map[name]
                    s["score"] = score
                    s["match_reason"] = m.get("reason", "")
                    matched_skills.append(s)

            matched_skills.sort(key=lambda x: x["score"], reverse=True)
            return matched_skills

        except Exception as e:
            logger.warning(
                f"AI skill search failed ({e}), falling back to local fuzzy search."
            )

            # Local Fallback
            matched = []
            query_parts = query_lower.split()

            for skill in skills:
                name = skill["name"].lower()
                desc = skill["description"].lower() if skill.get("description") else ""

                # Keyword match
                if all(word in name for word in query_parts):
                    skill["score"] = 1.0
                    matched.append(skill)
                    continue

                if all(word in desc for word in query_parts):
                    skill["score"] = 0.8
                    matched.append(skill)
                    continue

                # Fuzzy match (Use original string query for SequenceMatcher)
                ratio_name = difflib.SequenceMatcher(None, query_lower, name).ratio()
                ratio_desc = 0
                if len(desc) < 100:
                    ratio_desc = difflib.SequenceMatcher(
                        None, query_lower, desc
                    ).ratio()

                score = max(ratio_name, ratio_desc)
                if score >= threshold:
                    skill["score"] = score
                    matched.append(skill)

            matched.sort(key=lambda x: x.get("score", 0), reverse=True)
            return matched

    def get_skill(self, skill_name: str) -> Optional[Dict]:
        """获取 Skill 完整信息"""
        index = self.get_skill_index()
        return index.get(skill_name)

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

    def register_skill_handlers(self, adapter_manager: Any):
        """
        动态注册所有 Skill 的 Handlers (Commands, Callbacks, etc.)
        """
        import importlib.util

        index = self.get_skill_index()
        for skill_name, info in index.items():
            scripts = info.get("scripts", [])
            target_script = "execute.py"

            if target_script not in scripts:
                continue

            skill_dir = info["skill_dir"]
            script_path = os.path.join(skill_dir, "scripts", target_script)

            try:
                # 动态加载模块
                module_name = f"skills.{info['source']}.{skill_name}.scripts.execute"

                # 如果已经加载过，尝试从缓存获取 (或者强制重新加载)
                # 这里我们使用 importlib 动态加载文件路径
                spec = importlib.util.spec_from_file_location(module_name, script_path)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)

                    # 检查是否有 register_handlers 函数
                    if hasattr(module, "register_handlers"):
                        logger.info(f"🔌 Registering handlers for skill: {skill_name}")
                        module.register_handlers(adapter_manager)
                        self._loaded_modules[skill_name] = module  # 缓存模块
                    else:
                        pass
            except Exception as e:
                logger.error(
                    f"❌ Failed to register handlers for skill {skill_name}: {e}"
                )

    def import_skill_module(
        self, skill_name: str, script_name: str = "execute.py"
    ) -> Optional[Any]:
        """
        Dynamically import a module from a skill's scripts directory.
        Used to access internal logic of skills without hard dependencies.
        """
        skill_info = self.get_skill(skill_name)
        if not skill_info:
            logger.warning(f"Skill not found: {skill_name}")
            return None

        skill_dir = skill_info["skill_dir"]
        script_path = os.path.join(skill_dir, "scripts", script_name)

        if not os.path.exists(script_path):
            logger.warning(f"Script not found: {script_path}")
            return None

        try:
            import importlib.util
            import sys

            # Module name must be unique to avoid collisions
            module_name = (
                f"skills.dynamic.{skill_name}.{script_name.replace('.py', '')}"
            )

            spec = importlib.util.spec_from_file_location(module_name, script_path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)

                # Add script dir to sys.path temporarily to allow relative imports (like importing siblings)
                script_dir = os.path.dirname(script_path)
                if script_dir not in sys.path:
                    sys.path.insert(0, script_dir)

                spec.loader.exec_module(module)
                return module
        except Exception as e:
            logger.error(
                f"Failed to import skill module {skill_name}/{script_name}: {e}"
            )
            return None


# 全局单例
skill_loader = SkillLoader()
