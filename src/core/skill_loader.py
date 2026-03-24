"""Skill loader with unified protocol metadata (v3-first)."""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


def _normalize_text_list(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    rows: List[str] = []
    for item in value:
        token = str(item or "").strip()
        if token and token not in rows:
            rows.append(token)
    return rows


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _clean_code_token(value: str) -> str:
    return str(value or "").strip().strip("`").strip()


def _schema_for_value(value: Any) -> Dict[str, Any]:
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, list):
        item_schema = _schema_for_value(value[0]) if value else {"type": "string"}
        return {"type": "array", "items": item_schema}
    if isinstance(value, dict):
        properties = {
            str(key): _schema_for_value(item) for key, item in dict(value).items()
        }
        return {"type": "object", "properties": properties}
    return {"type": "string"}


def _schema_type(type_name: str) -> Dict[str, Any]:
    normalized = str(type_name or "").strip().lower()
    if normalized in {"list", "array"}:
        return {"type": "array", "items": {"type": "string"}}
    if normalized in {"int", "integer"}:
        return {"type": "integer"}
    if normalized in {"float", "number"}:
        return {"type": "number"}
    if normalized in {"bool", "boolean"}:
        return {"type": "boolean"}
    if normalized in {"object", "dict", "map"}:
        return {"type": "object", "properties": {}}
    return {"type": "string"}


def _infer_schema_from_parameter_table(markdown_content: str) -> Dict[str, Any]:
    lines = [line.rstrip() for line in str(markdown_content or "").splitlines()]
    for idx in range(len(lines) - 1):
        header = [part.strip() for part in lines[idx].strip().strip("|").split("|")]
        if len(header) < 4:
            continue
        if "参数" not in header[0] or "类型" not in header[1] or "必" not in header[2]:
            continue
        if "---" not in lines[idx + 1] and ":---" not in lines[idx + 1]:
            continue

        properties: Dict[str, Any] = {}
        required: List[str] = []
        cursor = idx + 2
        while cursor < len(lines):
            raw = lines[cursor].strip()
            if not raw.startswith("|"):
                break
            parts = [part.strip() for part in raw.strip("|").split("|")]
            if len(parts) < 4:
                break
            name = _clean_code_token(parts[0])
            type_name = _clean_code_token(parts[1])
            required_flag = _clean_code_token(parts[2]).lower()
            description = parts[3].strip()
            if not name:
                cursor += 1
                continue

            field_schema = _schema_type(type_name)
            enum_values = [
                token.strip()
                for token in re.findall(r"`([^`]+)`", description)
                if token.strip()
            ]
            if len(enum_values) > 1 and field_schema.get("type") == "string":
                field_schema["enum"] = enum_values
            properties[name] = field_schema
            if required_flag in {"是", "yes", "true", "required"}:
                required.append(name)
            cursor += 1

        if properties:
            schema: Dict[str, Any] = {"type": "object", "properties": properties}
            if required:
                schema["required"] = required
            return schema
    return {"type": "object", "properties": {}}


def _infer_schema_from_json_examples(markdown_content: str) -> Dict[str, Any]:
    pattern = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
    for match in pattern.finditer(str(markdown_content or "")):
        try:
            loaded = json.loads(match.group(1))
        except Exception:
            continue
        if isinstance(loaded, dict):
            return _schema_for_value(loaded)
    return {"type": "object", "properties": {}}


def _normalize_input_schema(frontmatter_value: Any, markdown_content: str) -> Dict[str, Any]:
    schema = frontmatter_value if isinstance(frontmatter_value, dict) else {}
    base: Dict[str, Any] = {
        "type": str(schema.get("type") or "object").strip() or "object",
        "properties": dict(schema.get("properties") or {}),
    }
    if isinstance(schema.get("required"), list):
        base["required"] = [str(item).strip() for item in schema["required"] if str(item).strip()]

    if base["properties"]:
        return base

    inferred = _infer_schema_from_parameter_table(markdown_content)
    if inferred.get("properties"):
        return inferred

    inferred = _infer_schema_from_json_examples(markdown_content)
    if inferred.get("properties"):
        return inferred
    return base


def _normalize_policy_groups(value: Any) -> List[str]:
    rows: List[str] = []
    for item in _normalize_text_list(value):
        token = str(item or "").strip().lower()
        if not token:
            continue
        if not token.startswith("group:"):
            token = f"group:{token}"
        if token not in rows:
            rows.append(token)
    return rows


def _normalize_skill_alias(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    safe_chars: list[str] = []
    for ch in raw:
        if ch.isalnum() or ch in {"-", "_"}:
            safe_chars.append(ch)
        else:
            safe_chars.append("_")
    token = "".join(safe_chars).replace("-", "_")
    while "__" in token:
        token = token.replace("__", "_")
    return token.strip("_")


def _normalize_tool_exports(
    *,
    frontmatter: Dict[str, Any],
    markdown_content: str,
    skill_name: str,
    skill_description: str,
    skill_input_schema: Dict[str, Any],
) -> List[Dict[str, Any]]:
    raw_exports = frontmatter.get("tool_exports")
    if isinstance(raw_exports, dict):
        raw_exports = [raw_exports]
    if not isinstance(raw_exports, list):
        return []

    exports: List[Dict[str, Any]] = []
    for item in raw_exports:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        description = str(item.get("description") or skill_description or "").strip()
        handler = str(item.get("handler") or name).strip()
        parameters_seed = item.get("parameters")
        if not isinstance(parameters_seed, dict):
            parameters_seed = item.get("input_schema")
        parameters = (
            _normalize_input_schema(parameters_seed, markdown_content)
            if isinstance(parameters_seed, dict)
            else dict(skill_input_schema or {})
        )
        exports.append(
            {
                "name": name,
                "description": description,
                "parameters": parameters,
                "handler": handler,
                "skill_name": skill_name,
                "prompt_hint": str(item.get("prompt_hint") or "").strip(),
                "usage_tags": _normalize_text_list(item.get("usage_tags")),
                "policy_groups": _normalize_policy_groups(item.get("policy_groups")),
            }
        )
    return exports


def _build_skill_contract(
    *,
    source: str,
    manager_only: bool,
    allowed_roles: List[str],
    frontmatter: Dict[str, Any],
    permissions: Dict[str, Any],
) -> Dict[str, Any]:
    runtime_target = str(frontmatter.get("runtime_target") or "").strip().lower()
    if runtime_target not in {"manager", "subagent"}:
        if manager_only or allowed_roles == ["manager"]:
            runtime_target = "manager"
        elif allowed_roles == ["subagent"]:
            runtime_target = "subagent"
        else:
            runtime_target = "manager"

    change_level = str(frontmatter.get("change_level") or "").strip().lower()
    if change_level not in {"learned", "builtin"}:
        change_level = "learned" if source == "learned" else "builtin"

    allow_manager_modify = _as_bool(
        frontmatter.get("allow_manager_modify"),
        default=change_level in {"learned", "builtin"},
    )
    allow_auto_publish = _as_bool(
        frontmatter.get("allow_auto_publish"),
        default=change_level == "learned",
    )
    rollout_target = str(frontmatter.get("rollout_target") or "").strip().lower()
    if rollout_target not in {"manager", "subagent", "api", "none"}:
        rollout_target = runtime_target if runtime_target == "subagent" else "manager"

    return {
        "runtime_target": runtime_target,
        "change_level": change_level,
        "allow_manager_modify": allow_manager_modify,
        "allow_auto_publish": allow_auto_publish,
        "rollout_target": rollout_target,
        "dependencies": _normalize_text_list(frontmatter.get("dependencies")),
        "preflight_commands": _normalize_text_list(
            frontmatter.get("preflight_commands")
        ),
        "permissions": dict(permissions or {}),
        "allowed_roles": list(allowed_roles or []),
    }


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
        self._skill_aliases: Dict[str, str] = {}
        self._tree_fingerprint: tuple[tuple[str, int], ...] = ()

    def _compute_tree_fingerprint(self) -> tuple[tuple[str, int], ...]:
        root = Path(self.skills_dir)
        if not root.exists():
            return ()

        rows: list[tuple[str, int]] = []
        for path in root.glob("**/SKILL.md"):
            try:
                rel = str(path.relative_to(root)).replace("\\", "/")
                rows.append((rel, int(path.stat().st_mtime_ns)))
            except Exception:
                continue
        rows.sort()
        return tuple(rows)

    def refresh_if_changed(self) -> Dict[str, Dict[str, Any]]:
        fingerprint = self._compute_tree_fingerprint()
        if not self._skill_index or fingerprint != self._tree_fingerprint:
            self.scan_skills()
        return self._skill_index

    def scan_skills(self) -> Dict[str, Dict[str, Any]]:
        self._skill_index.clear()
        self._skill_aliases.clear()

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
                for alias in {
                    str(parsed["name"] or "").strip(),
                    str(os.path.basename(skill_dir) or "").strip(),
                    _normalize_skill_alias(parsed.get("name")),
                    _normalize_skill_alias(os.path.basename(skill_dir)),
                }:
                    safe_alias = str(alias or "").strip()
                    if safe_alias:
                        self._skill_aliases[safe_alias] = parsed["name"]

        self._tree_fingerprint = self._compute_tree_fingerprint()

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

        triggers = _normalize_text_list(frontmatter.get("triggers") or [])
        input_schema = _normalize_input_schema(
            frontmatter.get("input_schema"),
            markdown_content,
        )
        policy_groups = _normalize_policy_groups(frontmatter.get("policy_groups"))
        platform_handlers = _as_bool(
            frontmatter.get("platform_handlers"),
            default=False,
        )
        scheduled_jobs = _as_bool(
            frontmatter.get("scheduled_jobs"),
            default=False,
        )
        tool_exports = _normalize_tool_exports(
            frontmatter=frontmatter,
            markdown_content=markdown_content,
            skill_name=name,
            skill_description=description,
            skill_input_schema=input_schema,
        )
        allowed_tools = _normalize_text_list(
            frontmatter.get("allowed-tools") or frontmatter.get("allowed_tools") or []
        )

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
        allowed_roles = [
            str(item or "").strip().lower()
            for item in _normalize_text_list(frontmatter.get("allowed_roles") or [])
            if str(item or "").strip()
        ]

        permissions = frontmatter.get("permissions")
        permissions_obj = dict(permissions) if isinstance(permissions, dict) else {}
        contract = _build_skill_contract(
            source=source,
            manager_only=manager_only,
            allowed_roles=allowed_roles,
            frontmatter=frontmatter,
            permissions=permissions_obj,
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
            "allowed_tools": allowed_tools,
            "input_schema": input_schema,
            "tool_exports": tool_exports,
            "policy_groups": policy_groups,
            "platform_handlers": platform_handlers,
            "scheduled_jobs": scheduled_jobs,
            "permissions": permissions_obj,
            "manager_only": manager_only,
            "allowed_roles": allowed_roles,
            "contract": contract,
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
        self.refresh_if_changed()
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
                    "tool_exports": [dict(item) for item in info.get("tool_exports") or []],
                    "policy_groups": list(info.get("policy_groups") or []),
                    "platform_handlers": bool(info.get("platform_handlers")),
                    "permissions": dict(info.get("permissions") or {}),
                    "manager_only": bool(info.get("manager_only")),
                    "allowed_roles": list(info.get("allowed_roles") or []),
                    "input_schema": info.get("input_schema", {}),
                    "contract": dict(info.get("contract") or {}),
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
            alias_name = _normalize_skill_alias(name)
            alias_query = _normalize_skill_alias(query_lower)

            score = max(
                difflib.SequenceMatcher(None, query_lower, name).ratio(),
                difflib.SequenceMatcher(None, alias_query, alias_name).ratio()
                if alias_query and alias_name
                else 0.0,
                difflib.SequenceMatcher(None, query_lower, desc[:300]).ratio()
                if desc
                else 0.0,
                difflib.SequenceMatcher(None, query_lower, trigger_text[:300]).ratio()
                if trigger_text
                else 0.0,
            )

            if query_lower in name or (alias_query and alias_query in alias_name):
                score = max(score, 1.0)

            if score >= threshold:
                cloned = dict(skill)
                cloned["score"] = score
                matched.append(cloned)

        matched.sort(key=lambda item: item.get("score", 0.0), reverse=True)
        return matched

    def get_skill(self, skill_name: str) -> Optional[Dict[str, Any]]:
        safe_name = str(skill_name or "").strip()
        if not safe_name:
            return None
        index = self.get_skill_index()
        direct = index.get(safe_name)
        if direct is not None:
            return direct
        alias_key = _normalize_skill_alias(safe_name)
        mapped = self._skill_aliases.get(alias_key) or self._skill_aliases.get(safe_name)
        if not mapped:
            return None
        return index.get(mapped)

    def get_tool_exports(self) -> List[Dict[str, Any]]:
        exports: List[Dict[str, Any]] = []
        for info in self.get_skill_index().values():
            allowed_roles = [
                str(item or "").strip().lower()
                for item in list(info.get("allowed_roles") or [])
                if str(item or "").strip()
            ]
            for item in list(info.get("tool_exports") or []):
                exported = dict(item or {})
                exported.setdefault("skill_name", str(info.get("name") or "").strip())
                exported.setdefault("allowed_roles", list(allowed_roles))
                exported.setdefault("manager_only", bool(info.get("manager_only")))
                exported.setdefault(
                    "policy_groups",
                    list(info.get("policy_groups") or []),
                )
                exports.append(exported)
        return exports

    def get_tool_export(self, tool_name: str) -> Optional[Dict[str, Any]]:
        safe_name = str(tool_name or "").strip()
        if not safe_name:
            return None
        for item in self.get_tool_exports():
            if str(item.get("name") or "").strip() == safe_name:
                return item
        return None

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

    def _load_skill_script_module(
        self,
        *,
        skill_name: str,
        skill_info: Dict[str, Any],
        script_name: str = "execute.py",
    ) -> Optional[Any]:
        cached = self._loaded_modules.get(skill_name)
        if cached is not None and script_name == "execute.py":
            return cached

        scripts = skill_info.get("scripts", [])
        if script_name not in scripts:
            return None

        script_path = os.path.join(skill_info["skill_dir"], "scripts", script_name)
        if not os.path.exists(script_path):
            return None

        try:
            import importlib.util
            import sys

            module_name = (
                f"skills.{skill_info['source']}.{skill_name}.scripts."
                f"{script_name.replace('.py', '')}"
            )
            spec = importlib.util.spec_from_file_location(module_name, script_path)
            if not spec or not spec.loader:
                return None

            module = importlib.util.module_from_spec(spec)
            script_dir = os.path.dirname(script_path)
            if script_dir not in sys.path:
                sys.path.insert(0, script_dir)
            spec.loader.exec_module(module)

            if script_name == "execute.py":
                self._loaded_modules[skill_name] = module
            return module
        except Exception as exc:
            logger.error(
                "Failed to import skill module %s/%s: %s",
                skill_name,
                script_name,
                exc,
            )
            return None

    def register_skill_handlers(self, adapter_manager: Any):
        for skill_name, info in self.get_skill_index().items():
            if not bool(info.get("platform_handlers")):
                continue

            module = self._load_skill_script_module(
                skill_name=skill_name,
                skill_info=info,
            )
            if module is None or not hasattr(module, "register_handlers"):
                continue

            try:
                module.register_handlers(adapter_manager)
            except Exception as exc:
                logger.error(
                    "Failed to register handlers for skill %s: %s",
                    skill_name,
                    exc,
                )

    def register_skill_jobs(self, scheduler: Any):
        for skill_name, info in self.get_skill_index().items():
            if not bool(info.get("scheduled_jobs")):
                continue

            module = self._load_skill_script_module(
                skill_name=skill_name,
                skill_info=info,
            )
            if module is None or not hasattr(module, "register_jobs"):
                continue

            try:
                module.register_jobs(scheduler)
            except Exception as exc:
                logger.error(
                    "Failed to register jobs for skill %s: %s",
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

        module = self._load_skill_script_module(
            skill_name=skill_name,
            skill_info=skill_info,
            script_name=script_name,
        )
        if module is None:
            script_path = os.path.join(skill_info["skill_dir"], "scripts", script_name)
            logger.warning("Script not found or failed to import: %s", script_path)
        return module


skill_loader = SkillLoader()
