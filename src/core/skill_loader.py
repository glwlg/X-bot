"""Skill loader with unified protocol metadata (v3-first)."""

import logging
import os
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


class SkillLoader:
    """Skill dynamic loader with markdown-frontmatter indexing."""

    REQUIRED_V3_FIELDS = {
        "api_version",
        "name",
        "description",
        "triggers",
        "input_schema",
        "permissions",
        "entrypoint",
    }

    def __init__(self, skills_dir: str | None = None):
        if skills_dir is None:
            base_dir = os.path.dirname(__file__)
            docker_path = os.path.join(base_dir, "..", "skills")
            local_path = os.path.join(base_dir, "..", "..", "skills")
            resolved_dir = local_path if os.path.isdir(local_path) else docker_path
        else:
            resolved_dir = str(skills_dir)

        self.skills_dir = os.path.abspath(resolved_dir)
        logger.info(f"Using skills directory: {self.skills_dir}")

        self._loaded_modules: Dict[str, Any] = {}
        self._skill_index: Dict[str, Dict[str, Any]] = {}

    def scan_skills(self) -> Dict[str, Dict[str, Any]]:
        self._skill_index.clear()

        for subdir in ["builtin", "learned"]:
            dir_path = os.path.join(self.skills_dir, subdir)
            if not os.path.isdir(dir_path):
                continue

            for entry in os.listdir(dir_path):
                skill_dir = os.path.join(dir_path, entry)
                if not os.path.isdir(skill_dir):
                    continue

                skill_md_path = os.path.join(skill_dir, "SKILL.md")
                if not os.path.exists(skill_md_path):
                    continue

                parsed = self._parse_skill(skill_md_path, skill_dir, subdir)
                if not parsed:
                    continue

                self._skill_index[parsed["name"]] = parsed

        logger.info(
            "Total skills indexed: %s. Keys: %s",
            len(self._skill_index),
            list(self._skill_index.keys()),
        )
        return self._skill_index

    def _parse_skill(
        self,
        skill_md_path: str,
        skill_dir: str,
        source: str,
    ) -> Optional[Dict[str, Any]]:
        try:
            with open(skill_md_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as exc:
            logger.error("Failed to read skill file %s: %s", skill_md_path, exc)
            return None

        frontmatter: Dict[str, Any] = {}
        markdown_content = content

        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter = yaml.safe_load(parts[1]) or {}
                markdown_content = parts[2].strip()
            else:
                logger.warning("Invalid frontmatter format: %s", skill_md_path)

        name = str(frontmatter.get("name") or os.path.basename(skill_dir)).strip()
        description = str(frontmatter.get("description") or "").strip()

        triggers = frontmatter.get("triggers") or []
        if isinstance(triggers, str):
            triggers = [triggers]
        if not isinstance(triggers, list):
            triggers = []

        # We no longer parse input_schema and allowed_tools dynamically.
        # Skills are now just markdown documents for LLM reading.
        # But we still return a minimal representation for the UI or other basic needs.
        input_schema = frontmatter.get("input_schema") or {"type": "object", "properties": {}}
        allowed_tools = []

        manager_only_raw: Any = frontmatter.get("manager_only")
        if manager_only_raw is None:
            manager_only_raw = frontmatter.get("internal_only")
        if manager_only_raw is None:
            visibility = str(frontmatter.get("visibility") or "").strip().lower()
            manager_only_raw = visibility == "manager_only"

        if isinstance(manager_only_raw, bool):
            manager_only = manager_only_raw
        else:
            manager_only_text = str(manager_only_raw or "").strip().lower()
            manager_only = manager_only_text in {"1", "true", "yes", "on"}

        api_version = str(frontmatter.get("api_version") or "v3")
        allowed_roles_raw = frontmatter.get("allowed_roles") or []
        if isinstance(allowed_roles_raw, str):
            allowed_roles_raw = [allowed_roles_raw]
        if not isinstance(allowed_roles_raw, list):
            allowed_roles_raw = []
        allowed_roles = []
        for item in allowed_roles_raw:
            role = str(item or "").strip().lower()
            if role and role not in allowed_roles:
                allowed_roles.append(role)

        scripts = []
        scripts_dir = os.path.join(skill_dir, "scripts")
        if os.path.isdir(scripts_dir):
            scripts = sorted(
                [
                    filename
                    for filename in os.listdir(scripts_dir)
                    if filename.endswith(".py")
                ]
            )

        return {
            "api_version": api_version,
            "name": name,
            "description": description,
            "triggers": triggers,
            "allowed_tools": allowed_tools,
            "input_schema": input_schema,
            "manager_only": manager_only,
            "allowed_roles": allowed_roles,
            "cron_instruction": frontmatter.get("cron_instruction"),
            "license": frontmatter.get("license", ""),
            "entrypoint": str(frontmatter.get("entrypoint") or "").strip(),
            "skill_md_path": skill_md_path,
            "skill_md_content": markdown_content,
            "skill_dir": skill_dir,
            "scripts": scripts,
            "source": source,
        }

    # 移除旧的 schema 提取相关方法 (525行)

    def get_skill_index(self) -> Dict[str, Dict[str, Any]]:
        if not self._skill_index:
            self.scan_skills()
        return self._skill_index

    def get_skills_summary(self) -> List[Dict[str, Any]]:
        summary: List[Dict[str, Any]] = []
        for info in self.get_skill_index().values():
            summary.append(
                {
                    "name": info.get("name", ""),
                    "description": info.get("description", "")[:500],
                    "triggers": info.get("triggers", []),
                    "allowed_tools": info.get("allowed_tools", []),
                    "manager_only": bool(info.get("manager_only")),
                    "allowed_roles": list(info.get("allowed_roles") or []),
                    "input_schema": info.get("input_schema", {}),
                }
            )
        return summary

    async def find_similar_skills(
        self,
        query: str,
        threshold: float = 0.4,
    ) -> List[Dict[str, Any]]:
        import difflib

        query_lower = query.lower().strip()
        if not query_lower:
            return []

        skills = self.get_skills_summary()
        matched: List[Dict[str, Any]] = []

        for skill in skills:
            name = skill.get("name", "").lower()
            desc = skill.get("description", "").lower()
            trigger_text = " ".join(map(str, skill.get("triggers") or [])).lower()

            score = max(
                difflib.SequenceMatcher(None, query_lower, name).ratio(),
                difflib.SequenceMatcher(None, query_lower, desc[:300]).ratio()
                if desc
                else 0.0,
                difflib.SequenceMatcher(None, query_lower, trigger_text[:300]).ratio()
                if trigger_text
                else 0.0,
            )

            if query_lower in name:
                score = max(score, 1.0)

            if score >= threshold:
                cloned = dict(skill)
                cloned["score"] = score
                matched.append(cloned)

        matched.sort(key=lambda item: item.get("score", 0.0), reverse=True)
        return matched

    def get_skill(self, skill_name: str) -> Optional[Dict[str, Any]]:
        return self.get_skill_index().get(skill_name)

    def get_skill_md_content(self, skill_name: str) -> str:
        """Read the full raw markdown content for a loaded skill directory without parsing"""
        skill_info = self.get_skill(skill_name)
        if not skill_info:
            return ""
        
        return skill_info.get("skill_md_content", "")

    def reload_skills(self):
        self._loaded_modules.clear()
        self.scan_skills()

    def unload_skill(self, skill_name: str) -> bool:
        if skill_name in self._loaded_modules:
            del self._loaded_modules[skill_name]
            logger.info("Unloaded skill: %s", skill_name)
            return True
        return False

    def register_skill_handlers(self, adapter_manager: Any):
        import importlib.util

        for skill_name, info in self.get_skill_index().items():
            scripts = info.get("scripts", [])
            if "execute.py" not in scripts:
                continue

            script_path = os.path.join(info["skill_dir"], "scripts", "execute.py")
            module_name = f"skills.{info['source']}.{skill_name}.scripts.execute"

            try:
                spec = importlib.util.spec_from_file_location(module_name, script_path)
                if not spec or not spec.loader:
                    continue

                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                if hasattr(module, "register_handlers"):
                    module.register_handlers(adapter_manager)
                    self._loaded_modules[skill_name] = module
            except Exception as exc:
                logger.error(
                    "Failed to register handlers for skill %s: %s",
                    skill_name,
                    exc,
                )

    def import_skill_module(
        self,
        skill_name: str,
        script_name: str = "execute.py",
    ) -> Optional[Any]:
        skill_info = self.get_skill(skill_name)
        if not skill_info:
            logger.warning("Skill not found: %s", skill_name)
            return None

        script_path = os.path.join(skill_info["skill_dir"], "scripts", script_name)
        if not os.path.exists(script_path):
            logger.warning("Script not found: %s", script_path)
            return None

        try:
            import importlib.util
            import sys

            module_name = (
                f"skills.dynamic.{skill_name}.{script_name.replace('.py', '')}"
            )
            spec = importlib.util.spec_from_file_location(module_name, script_path)
            if not spec or not spec.loader:
                return None

            module = importlib.util.module_from_spec(spec)
            script_dir = os.path.dirname(script_path)
            if script_dir not in sys.path:
                sys.path.insert(0, script_dir)
            spec.loader.exec_module(module)
            return module
        except Exception as exc:
            logger.error(
                "Failed to import skill module %s/%s: %s",
                skill_name,
                script_name,
                exc,
            )
            return None


skill_loader = SkillLoader()
