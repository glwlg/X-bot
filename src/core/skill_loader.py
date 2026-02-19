"""Skill loader with unified protocol metadata (v3-first)."""

import logging
import os
import json
import re
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

        input_schema = frontmatter.get("input_schema")
        if not input_schema:
            input_schema = self._legacy_params_to_schema(
                frontmatter.get("params") or {}
            )
        input_schema = self._ensure_object_schema(input_schema)
        inferred_schema = self._infer_schema_from_markdown(markdown_content)
        input_schema = self._merge_schemas(input_schema, inferred_schema)

        permissions = frontmatter.get("permissions")
        if not isinstance(permissions, dict):
            permissions = {
                "filesystem": "workspace",
                "shell": False,
                "network": "limited",
            }

        entrypoint = frontmatter.get("entrypoint") or "scripts/execute.py"
        api_version = str(frontmatter.get("api_version") or "v3")

        missing = sorted(
            [field for field in self.REQUIRED_V3_FIELDS if field not in frontmatter]
        )
        if missing:
            logger.warning(
                "Skill %s missing v3 fields %s; using compatibility defaults",
                name,
                missing,
            )

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
            "input_schema": input_schema,
            "permissions": permissions,
            "entrypoint": entrypoint,
            "cron_instruction": frontmatter.get("cron_instruction"),
            # Keep legacy fields for compatibility.
            "params": frontmatter.get("params", {}),
            "license": frontmatter.get("license", ""),
            "skill_md_path": skill_md_path,
            "skill_md_content": markdown_content,
            "skill_dir": skill_dir,
            "scripts": scripts,
            "source": source,
        }

    def _legacy_params_to_schema(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if not params:
            return {"type": "object", "properties": {}}

        properties: Dict[str, Any] = {}
        required: List[str] = []
        for key, value in params.items():
            schema_type = "string"
            description = ""

            if isinstance(value, dict):
                schema_type = value.get("type", "string")
                description = value.get("description", "")
                if value.get("required"):
                    required.append(key)
            elif isinstance(value, str):
                description = value

            properties[key] = {
                "type": schema_type,
                "description": description,
            }

        schema: Dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        return schema

    def _ensure_object_schema(self, schema: Any) -> Dict[str, Any]:
        normalized: Dict[str, Any]
        if isinstance(schema, dict):
            normalized = dict(schema)
        else:
            normalized = {}

        if "type" not in normalized:
            normalized["type"] = "object"
        properties = normalized.get("properties")
        normalized["properties"] = (
            dict(properties) if isinstance(properties, dict) else {}
        )
        required = normalized.get("required")
        normalized["required"] = list(required) if isinstance(required, list) else []
        return normalized

    def _merge_schemas(
        self,
        base_schema: Dict[str, Any],
        inferred_schema: Dict[str, Any],
    ) -> Dict[str, Any]:
        merged = self._ensure_object_schema(base_schema)
        inferred = self._ensure_object_schema(inferred_schema)

        base_props = merged.get("properties") or {}
        inferred_props = inferred.get("properties") or {}
        for key, value in inferred_props.items():
            if key not in base_props:
                base_props[key] = value

        required_items: List[str] = []
        for key in list(merged.get("required") or []) + list(
            inferred.get("required") or []
        ):
            token = str(key or "").strip()
            if token and token not in required_items:
                required_items.append(token)

        merged["properties"] = base_props
        merged["required"] = required_items
        return merged

    def _infer_schema_from_markdown(self, markdown_content: str) -> Dict[str, Any]:
        properties: Dict[str, Dict[str, Any]] = {}
        required: List[str] = []

        for row in self._extract_parameter_rows(markdown_content):
            name = self._normalize_parameter_name(row.get("name", ""))
            if not name:
                continue
            prop: Dict[str, Any] = {
                "type": self._normalize_schema_type(row.get("type", "string")),
            }
            description = str(row.get("description") or "").strip()
            if description:
                prop["description"] = description
                enum_values = self._infer_enum_values(description)
                if enum_values:
                    prop["enum"] = enum_values
            properties[name] = prop

            required_flag = str(row.get("required") or "").strip().lower()
            if (
                required_flag
                in {
                    "yes",
                    "y",
                    "true",
                    "required",
                    "必填",
                    "是",
                }
                and name not in required
            ):
                required.append(name)

        for example in self._extract_example_objects(markdown_content):
            for key, value in example.items():
                name = self._normalize_parameter_name(str(key))
                if not name:
                    continue
                if name not in properties:
                    inferred_type = self._infer_type_from_value(value)
                    properties[name] = {"type": inferred_type}

        schema: Dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        return schema

    def _extract_parameter_rows(self, markdown_content: str) -> List[Dict[str, str]]:
        rows: List[Dict[str, str]] = []
        headers: List[str] = []
        in_table = False

        for line in str(markdown_content or "").splitlines():
            raw = line.strip()
            if not raw.startswith("|"):
                in_table = False
                headers = []
                continue

            cols = [col.strip() for col in raw.strip("|").split("|")]
            if not cols:
                continue
            lowered = [col.lower() for col in cols]
            if any("参数" in col or "parameter" in col for col in lowered):
                headers = cols
                in_table = True
                continue
            if not in_table:
                continue
            if all(set(col) <= {"-", ":", " "} for col in cols):
                continue

            name_idx = self._find_header_index(
                headers, ["参数", "name", "field", "param"]
            )
            type_idx = self._find_header_index(headers, ["类型", "type"])
            req_idx = self._find_header_index(headers, ["必填", "required"])
            desc_idx = self._find_header_index(headers, ["说明", "description", "desc"])
            if name_idx < 0 or type_idx < 0 or req_idx < 0 or desc_idx < 0:
                continue
            max_idx = max(name_idx, type_idx, req_idx, desc_idx)
            if len(cols) <= max_idx:
                continue
            rows.append(
                {
                    "name": cols[name_idx],
                    "type": cols[type_idx],
                    "required": cols[req_idx],
                    "description": cols[desc_idx],
                }
            )
        return rows

    def _find_header_index(self, headers: List[str], hints: List[str]) -> int:
        for idx, item in enumerate(headers):
            lowered = str(item or "").strip().lower()
            if any(hint in lowered for hint in hints):
                return idx
        return -1

    def _normalize_parameter_name(self, raw_name: str) -> str:
        text = str(raw_name or "").strip()
        if not text:
            return ""
        if "`" in text:
            matches = re.findall(r"`([^`]+)`", text)
            if matches:
                text = matches[0]
        text = text.strip().strip('"').strip("'")
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_\-]*$", text):
            return ""
        return text

    def _normalize_schema_type(self, raw_type: str) -> str:
        lowered = str(raw_type or "").strip().lower()
        mapping = {
            "string": "string",
            "str": "string",
            "text": "string",
            "number": "number",
            "float": "number",
            "integer": "integer",
            "int": "integer",
            "bool": "boolean",
            "boolean": "boolean",
            "object": "object",
            "dict": "object",
            "array": "array",
            "list": "array",
        }
        return mapping.get(lowered, "string")

    def _infer_enum_values(self, description: str) -> List[str]:
        text = str(description or "").strip()
        if not text:
            return []
        values = re.findall(r"`([^`]+)`", text)
        cleaned: List[str] = []
        for item in values:
            token = str(item or "").strip()
            if not token or token in cleaned:
                continue
            cleaned.append(token)
        if len(cleaned) >= 2:
            return cleaned[:12]
        return []

    def _extract_example_objects(self, markdown_content: str) -> List[Dict[str, Any]]:
        blocks = re.findall(
            r"```json\s*(\{[\s\S]*?\})\s*```",
            str(markdown_content or ""),
            flags=re.IGNORECASE,
        )
        output: List[Dict[str, Any]] = []
        for block in blocks:
            try:
                loaded = json.loads(block)
            except Exception:
                continue
            if isinstance(loaded, dict):
                output.append(loaded)
        return output

    def _infer_type_from_value(self, value: Any) -> str:
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "number"
        if isinstance(value, list):
            return "array"
        if isinstance(value, dict):
            return "object"
        return "string"

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
