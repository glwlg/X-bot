from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List

from core.prompts import (
    DEFAULT_SYSTEM_PROMPT,
    MEMORY_MANAGEMENT_GUIDE,
    MANAGER_CORE_PROMPT,
)
from core.markdown_memory_store import markdown_memory_store
from core.soul_store import soul_store
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def _short_desc(text: str, limit: int = 88) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    first = raw.splitlines()[0].strip()
    if len(first) <= limit:
        return first
    return first[:limit].rstrip() + "..."


def _as_tool_name(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("name") or "").strip()
    name = getattr(item, "name", "")
    return str(name or "").strip()


def _as_tool_desc(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("description") or "").strip()
    desc = getattr(item, "description", "")
    return str(desc or "").strip()


def _normalize_text_list(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    output: List[str] = []
    for item in values:
        token = str(item or "").strip()
        if token and token not in output:
            output.append(token)
    return output


class PromptComposer:
    """
    Build minimal runtime instruction:
    1) default system prompt
    2) SOUL
    3) tool inventory
    """

    def compose_base(
        self,
        *,
        runtime_user_id: str,
        tools: Iterable[Any] | None = None,
        runtime_policy_ctx: Dict[str, Any] | None = None,
        mode: str = "chat",
    ) -> str:
        soul_payload = soul_store.resolve_for_runtime_user(str(runtime_user_id or ""))
        parts: List[str] = []
        if not soul_payload:
            parts.append(DEFAULT_SYSTEM_PROMPT.strip())
        parts.append("【SOUL】\n" + soul_payload.content.strip())
        # tool_inventory_text, memory_available = self._compose_tool_inventory(
        #     tools=tools,
        #     runtime_policy_ctx=runtime_policy_ctx,
        # )
        # parts.append(tool_inventory_text)
        # if memory_available:
        #     parts.append(MEMORY_MANAGEMENT_GUIDE.strip())

        # 如果是 manager 模式，添加 Manager 核心 Prompt
        if str(mode or "").strip().lower() == "manager":
            manager_memory = markdown_memory_store.load_manager_snapshot(max_chars=1200)
            if manager_memory:
                parts.append("【MANAGER 经验记忆】\n" + manager_memory)
            worker_pool_info = self._get_worker_pool_info()
            manager_prompt = MANAGER_CORE_PROMPT.format(
                worker_pool_info=worker_pool_info
            )
            logger.debug("Manager Prompt: \n" + manager_prompt)
            parts.append("\n" + manager_prompt)

        # 拼接当前日期
        parts.append("\n【当前时间】\n" + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        logger.info(
            "Final Prompt: \n"
            + "\n\n".join([item for item in parts if str(item).strip()]).strip()
        )

        return "\n\n".join([item for item in parts if str(item).strip()]).strip()

    def _compose_tool_inventory(
        self,
        *,
        tools: Iterable[Any] | None,
        runtime_policy_ctx: Dict[str, Any] | None = None,
    ) -> tuple[str, bool]:
        lines: List[str] = []
        seen: set[str] = set()
        for item in tools or []:
            name = _as_tool_name(item)
            if not name or name in seen:
                continue
            seen.add(name)
            desc = _short_desc(_as_tool_desc(item))
            if desc:
                lines.append(f"- `{name}`: {desc}")
            else:
                lines.append(f"- `{name}`")

        agent_kind = (
            str((runtime_policy_ctx or {}).get("agent_kind") or "").strip().lower()
        )
        policy = dict((runtime_policy_ctx or {}).get("policy") or {})
        tools_cfg = dict(policy.get("tools") or {})
        deny = [
            str(item).strip().lower()
            for item in (tools_cfg.get("deny") or [])
            if str(item).strip()
        ]

        memory_allowed = ("group:memory" not in deny) and agent_kind != "worker"

        if not lines:
            return "【可用工具】\n- (none)", memory_allowed
        return "【可用工具】\n" + "\n".join(lines[:40]), memory_allowed

    def _get_worker_pool_info(self) -> str:
        """获取 Worker 池信息，供 Manager 决策派发"""
        try:
            from core.worker_store import worker_registry

            workers: list
            read_unlocked = getattr(worker_registry, "_read_unlocked", None)
            if callable(read_unlocked):
                raw = read_unlocked()
                workers = (
                    list((raw.get("workers") or {}).values())
                    if isinstance(raw, dict)
                    else []
                )
            else:
                path = worker_registry.meta_path
                if not path.exists():
                    return "当前没有可用 Worker"
                raw = json.loads(path.read_text(encoding="utf-8"))
                workers = (
                    list((raw.get("workers") or {}).values())
                    if isinstance(raw, dict)
                    else []
                )
            return self._format_worker_list(workers)
        except Exception as e:
            return f"获取 Worker 池信息失败: {e}"

    def _format_worker_list(self, workers: list) -> str:
        if not workers:
            return "当前没有可用 Worker"
        worker_list = []
        for w in workers:
            worker_id = str(w.get("id") or "unknown").strip() or "unknown"
            name = str(w.get("name") or worker_id).strip() or worker_id
            status = str(w.get("status") or "unknown").strip() or "unknown"
            backend = str(w.get("backend") or "unknown").strip() or "unknown"
            summary = str(w.get("summary") or "").strip()
            capabilities = _normalize_text_list(w.get("capabilities"))
            allowed_skills = self._infer_worker_extension_skills(worker_id)
            capability_hint = "、".join(capabilities[:4])
            skill_hint = "、".join(allowed_skills[:4])

            if not summary:
                if allowed_skills:
                    summary = f"可执行 {len(allowed_skills)} 个技能，示例：{skill_hint}"
                elif capabilities:
                    summary = f"擅长：{capability_hint}"
                else:
                    summary = "通用执行助手，可处理跨工具任务"

            ability_parts: List[str] = []
            if capability_hint:
                ability_parts.append(f"capabilities={capability_hint}")
            if skill_hint:
                ability_parts.append(f"skills={skill_hint}")
            abilities = ", ".join(ability_parts) if ability_parts else "skills=auto"
            worker_list.append(
                f"- {name} (worker_id={worker_id}): "
                f"状态={status}, 后端={backend}, 简介={summary}, {abilities}"
            )
        return "\n".join(worker_list)

    def _infer_worker_extension_skills(self, worker_id: str) -> List[str]:
        safe_worker_id = str(worker_id or "").strip()
        if not safe_worker_id:
            return []

        try:
            from core.skill_loader import skill_loader
            from core.tool_access_store import tool_access_store

            runtime_user_id = f"worker::{safe_worker_id}::prompt"
            allowed_skills: List[str] = []
            for item in skill_loader.get_skills_summary():
                skill_name = str(item.get("name") or "").strip()
                if not skill_name:
                    continue
                tool_name = f"ext_{skill_name.replace('-', '_')}"
                allowed, _detail = tool_access_store.is_tool_allowed(
                    runtime_user_id=runtime_user_id,
                    platform="worker_runtime",
                    tool_name=tool_name,
                    kind="tool",
                )
                if allowed:
                    allowed_skills.append(skill_name)
            return allowed_skills
        except Exception:
            return []


prompt_composer = PromptComposer()
