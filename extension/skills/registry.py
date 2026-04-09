"""Skill registry backed by SKILL.md metadata."""

import inspect
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from core.extension_base import SkillExtension

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
    ikaros_only: bool,
    allowed_roles: List[str],
    frontmatter: Dict[str, Any],
    permissions: Dict[str, Any],
) -> Dict[str, Any]:
    runtime_target = str(frontmatter.get("runtime_target") or "").strip().lower()
    if runtime_target not in {"ikaros", "subagent"}:
        if ikaros_only or allowed_roles == ["ikaros"]:
            runtime_target = "ikaros"
        elif allowed_roles == ["subagent"]:
            runtime_target = "subagent"
        else:
            runtime_target = "ikaros"

    change_level = str(frontmatter.get("change_level") or "").strip().lower()
    if change_level not in {"learned", "builtin"}:
        change_level = "learned" if source == "learned" else "builtin"

    allow_ikaros_modify = _as_bool(
        frontmatter.get("allow_ikaros_modify"),
        default=change_level in {"learned", "builtin"},
    )
    allow_auto_publish = _as_bool(
        frontmatter.get("allow_auto_publish"),
        default=change_level == "learned",
    )
    rollout_target = str(frontmatter.get("rollout_target") or "").strip().lower()
    if rollout_target not in {"ikaros", "subagent", "api", "none"}:
        rollout_target = runtime_target if runtime_target == "subagent" else "ikaros"

    return {
        "runtime_target": runtime_target,
        "change_level": change_level,
        "allow_ikaros_modify": allow_ikaros_modify,
        "allow_auto_publish": allow_auto_publish,
        "rollout_target": rollout_target,
        "dependencies": _normalize_text_list(frontmatter.get("dependencies")),
        "preflight_commands": _normalize_text_list(
            frontmatter.get("preflight_commands")
        ),
        "permissions": dict(permissions or {}),
        "allowed_roles": list(allowed_roles or []),
    }


class SkillRegistry:
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
            resolved_dir = str(Path(__file__).resolve().parent)
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

        ikaros_only_raw: Any = frontmatter.get("ikaros_only")
        if ikaros_only_raw is None:
            ikaros_only_raw = frontmatter.get("internal_only")
        if ikaros_only_raw is None:
            visibility = str(frontmatter.get("visibility") or "").strip().lower()
            ikaros_only_raw = visibility == "ikaros_only"

        if isinstance(ikaros_only_raw, bool):
            ikaros_only = ikaros_only_raw
        else:
            ikaros_only_text = str(ikaros_only_raw or "").strip().lower()
            ikaros_only = ikaros_only_text in {"1", "true", "yes", "on"}

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
            ikaros_only=ikaros_only,
            allowed_roles=allowed_roles,
            frontmatter=frontmatter,
            permissions=permissions_obj,
        )

        scripts: list[str] = []
        scripts_dir = os.path.join(skill_dir, "scripts")
        if os.path.isdir(scripts_dir):
            scripts = sorted(
                str(path.relative_to(scripts_dir)).replace("\\", "/")
                for path in Path(scripts_dir).rglob("*.py")
                if "__pycache__" not in path.parts
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
            "ikaros_only": ikaros_only,
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

    def get_disabled_skill_names(self) -> set[str]:
        try:
            from core.runtime_config_store import runtime_config_store

            return {
                str(item).strip()
                for item in runtime_config_store.get_disabled_skills()
                if str(item).strip()
            }
        except Exception:
            return set()

    def is_skill_enabled(self, skill_name: str) -> bool:
        safe_name = str(skill_name or "").strip()
        if not safe_name:
            return False
        skill_info = self.get_skill(safe_name)
        canonical_name = str((skill_info or {}).get("name") or safe_name).strip()
        return canonical_name not in self.get_disabled_skill_names()

    def get_enabled_skill_index(self) -> Dict[str, Dict[str, Any]]:
        disabled = self.get_disabled_skill_names()
        return {
            name: info
            for name, info in self.get_skill_index().items()
            if str(name).strip() not in disabled
        }

    def get_skills_summary(self) -> List[Dict[str, Any]]:
        summary: List[Dict[str, Any]] = []
        for info in self.get_enabled_skill_index().values():
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
                    "ikaros_only": bool(info.get("ikaros_only")),
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

    def get_enabled_skill(self, skill_name: str) -> Optional[Dict[str, Any]]:
        skill_info = self.get_skill(skill_name)
        if not skill_info:
            return None
        skill_key = str(skill_info.get("name") or skill_name).strip()
        if not self.is_skill_enabled(skill_key):
            return None
        return skill_info

    def get_tool_exports(self) -> List[Dict[str, Any]]:
        exports: List[Dict[str, Any]] = []
        for info in self.get_enabled_skill_index().values():
            allowed_roles = [
                str(item or "").strip().lower()
                for item in list(info.get("allowed_roles") or [])
                if str(item or "").strip()
            ]
            for item in list(info.get("tool_exports") or []):
                exported = dict(item or {})
                exported.setdefault("skill_name", str(info.get("name") or "").strip())
                exported.setdefault("allowed_roles", list(allowed_roles))
                exported.setdefault("ikaros_only", bool(info.get("ikaros_only")))
                exported.setdefault("entrypoint", str(info.get("entrypoint") or "").strip())
                exported.setdefault("skill_dir", str(info.get("skill_dir") or "").strip())
                exported.setdefault("source", str(info.get("source") or "").strip())
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
        skill_info = self.get_enabled_skill(skill_name)
        if not skill_info:
            return ""

        return skill_info.get("skill_md_content", "")

    def reload_skills(self):
        self._loaded_modules.clear()
        self.scan_skills()

    def unload_skill(self, skill_name: str) -> bool:
        removed = False
        for cache_key in list(self._loaded_modules.keys()):
            if cache_key.startswith(f"{skill_name}:"):
                del self._loaded_modules[cache_key]
                removed = True
        if removed:
            logger.info("Unloaded skill: %s", skill_name)
        return removed

    def _load_skill_script_module(
        self,
        *,
        skill_name: str,
        skill_info: Dict[str, Any],
        script_name: str = "execute.py",
    ) -> Optional[Any]:
        cache_key = f"{skill_name}:{script_name}"
        cached = self._loaded_modules.get(cache_key)
        if cached is not None:
            return cached

        scripts = skill_info.get("scripts", [])
        if script_name not in scripts:
            return None

        script_path = os.path.join(skill_info["skill_dir"], "scripts", script_name)
        if not os.path.exists(script_path):
            return None

        try:
            import importlib.util
            import importlib.machinery
            import sys
            import types

            safe_skill = _normalize_skill_alias(skill_name) or "skill"
            raw_script_parts = list(Path(script_name).with_suffix("").parts)
            if raw_script_parts and raw_script_parts[-1] == "__init__":
                raw_script_parts = raw_script_parts[:-1]
            safe_parts = [
                _normalize_skill_alias(part) or "module" for part in raw_script_parts
            ]
            module_name = ".".join(
                [
                    "extension",
                    "skills",
                    "dynamic",
                    str(skill_info["source"] or "").strip(),
                    safe_skill,
                    "scripts",
                    *safe_parts,
                ]
            )

            def _ensure_namespace_package(name: str, path: str) -> None:
                existing = sys.modules.get(name)
                if existing is not None:
                    package_paths = list(getattr(existing, "__path__", []))
                    if path not in package_paths:
                        package_paths.append(path)
                        existing.__path__ = package_paths
                    return
                package = types.ModuleType(name)
                package.__file__ = path
                package.__path__ = [path]
                package.__package__ = name
                package.__spec__ = importlib.machinery.ModuleSpec(
                    name,
                    loader=None,
                    is_package=True,
                )
                package.__spec__.submodule_search_locations = [path]
                sys.modules[name] = package

            skill_dir = os.path.join(skill_info["skill_dir"])
            scripts_root = os.path.join(skill_dir, "scripts")
            source_root = os.path.dirname(skill_dir)
            skills_root = os.path.dirname(source_root)
            _ensure_namespace_package("extension.skills.dynamic", skills_root)
            _ensure_namespace_package(
                f"extension.skills.dynamic.{str(skill_info['source'] or '').strip()}",
                source_root,
            )
            _ensure_namespace_package(
                f"extension.skills.dynamic.{str(skill_info['source'] or '').strip()}.{safe_skill}",
                skill_dir,
            )
            _ensure_namespace_package(
                f"extension.skills.dynamic.{str(skill_info['source'] or '').strip()}.{safe_skill}.scripts",
                scripts_root,
            )
            current_path = scripts_root
            package_name = (
                f"extension.skills.dynamic.{str(skill_info['source'] or '').strip()}."
                f"{safe_skill}.scripts"
            )
            for raw_part, safe_part in zip(raw_script_parts[:-1], safe_parts[:-1]):
                current_path = os.path.join(current_path, raw_part)
                package_name = f"{package_name}.{safe_part}"
                _ensure_namespace_package(package_name, current_path)

            spec_kwargs: Dict[str, Any] = {}
            if os.path.basename(script_path) == "__init__.py":
                spec_kwargs["submodule_search_locations"] = [os.path.dirname(script_path)]
            spec = importlib.util.spec_from_file_location(
                module_name,
                script_path,
                **spec_kwargs,
            )
            if not spec or not spec.loader:
                return None

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            script_dir = os.path.dirname(script_path)
            if script_dir not in sys.path:
                sys.path.insert(0, script_dir)
            spec.loader.exec_module(module)

            self._loaded_modules[cache_key] = module
            return module
        except Exception as exc:
            if "module_name" in locals():
                sys.modules.pop(module_name, None)
            logger.error(
                "Failed to import skill module %s/%s: %s",
                skill_name,
                script_name,
                exc,
            )
            return None

    def _load_skill_python_modules(self, skill_name: str, skill_info: Dict[str, Any]) -> list[Any]:
        modules: list[Any] = []
        scripts_dir = Path(str(skill_info.get("skill_dir") or "")) / "scripts"
        if not scripts_dir.is_dir():
            return modules

        for script_path in sorted(scripts_dir.rglob("*.py")):
            if "__pycache__" in script_path.parts:
                continue
            script_name = str(script_path.relative_to(scripts_dir)).replace("\\", "/")
            module = self._load_skill_script_module(
                skill_name=skill_name,
                skill_info=skill_info,
                script_name=script_name,
            )
            if module is not None:
                modules.append(module)
        return modules

    def _register_skill_management(self, runtime: Any) -> None:
        from handlers.skill_handlers import (
            WAITING_FOR_SKILL_DESC,
            handle_skill_callback,
            handle_teach_input,
            reload_skills_command,
            skills_command,
            teach_command,
        )
        from handlers import cancel

        runtime.register_command("skills", skills_command, description="查看可用技能")
        runtime.register_command(
            "reload_skills",
            reload_skills_command,
            description="重载技能",
        )
        runtime.register_callback("^skill_", handle_skill_callback)
        runtime.register_callback("^skills_", handle_skill_callback)

        if not runtime.has_adapter("telegram"):
            return

        try:
            from telegram.ext import ConversationHandler, filters

            tg_adapter = runtime.get_adapter("telegram")
            tg_app = getattr(tg_adapter, "application", None)
            if tg_app is None:
                return

            teach_conv_handler = ConversationHandler(
                entry_points=[tg_adapter.create_command_handler("teach", teach_command)],
                states={
                    WAITING_FOR_SKILL_DESC: [
                        tg_adapter.create_message_handler(
                            filters.TEXT & ~filters.COMMAND,
                            handle_teach_input,
                        )
                    ],
                },
                fallbacks=[tg_adapter.create_command_handler("cancel", cancel)],
                per_message=False,
            )
            tg_app.add_handler(teach_conv_handler)
        except Exception:
            logger.warning("Failed to install Telegram teach skill flow.", exc_info=True)

    def register_extensions(self, runtime: Any) -> None:
        self.scan_skills()
        self._register_skill_management(runtime)

        for skill_name, info in self.get_enabled_skill_index().items():
            for module in self._load_skill_python_modules(skill_name, info):
                for _, obj in inspect.getmembers(module, inspect.isclass):
                    if (
                        issubclass(obj, SkillExtension)
                        and obj is not SkillExtension
                        and obj.__module__ == module.__name__
                    ):
                        extension = obj()
                        if extension.enabled(runtime):
                            extension.register(runtime)

    def import_skill_module(
        self,
        skill_name: str,
        script_name: str = "execute.py",
        *,
        include_disabled: bool = False,
    ) -> Optional[Any]:
        skill_info = (
            self.get_skill(skill_name)
            if include_disabled
            else self.get_enabled_skill(skill_name)
        )
        if not skill_info:
            if self.get_skill(skill_name):
                logger.warning("Skill is disabled: %s", skill_name)
            else:
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


skill_registry = SkillRegistry()
