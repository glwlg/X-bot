from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List

from core.prompts import (
    DEFAULT_SYSTEM_PROMPT,
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
        platform: str = "",
        tools: Iterable[Any] | None = None,
        runtime_policy_ctx: Dict[str, Any] | None = None,
        mode: str = "chat",
    ) -> str:
        soul_payload = soul_store.resolve_for_runtime_user(str(runtime_user_id or ""))
        parts: List[str] = []
        if not soul_payload:
            parts.append(DEFAULT_SYSTEM_PROMPT.strip())
        parts.append("【SOUL】\n" + soul_payload.content.strip())

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
        elif str(mode or "").strip().lower() == "media_image":
            parts.append(
                "\n【当前任务要求】\n这是一次图片分析请求。你需要保持你的角色语气，结合图片与用户指令完成任务。\n如果用户明确要求记账/入账，请优先调用 `ext_quick_accounting` 完成真实入账；其他场景优先直接给出分析结论，避免无关工具调用。"
            )

        # 注入 Skill 目录与 load_skill 使用引导
        skill_catalog = self._build_skill_catalog(
            runtime_user_id=runtime_user_id,
            platform=platform,
        )
        if skill_catalog:
            parts.append(skill_catalog)

        # 拼接当前日期
        # parts.append("\n【当前日期】\n" + datetime.now().strftime("%Y-%m-%d"))

        logger.info(
            "Final Prompt: \n"
            + "\n\n".join([item for item in parts if str(item).strip()]).strip()
        )

        return "\n\n".join([item for item in parts if str(item).strip()]).strip()

    @staticmethod
    def _runtime_role(runtime_user_id: str, platform: str) -> str:
        uid = str(runtime_user_id or "").strip().lower()
        platform_name = str(platform or "").strip().lower()
        if uid.startswith("worker::") or platform_name == "worker_kernel":
            return "worker"
        return "manager"

    def _build_skill_catalog(
        self,
        *,
        runtime_user_id: str = "",
        platform: str = "",
    ) -> str:
        """构建可用技能目录，引导 LLM 按需加载 SOP。"""
        try:
            from core.skill_loader import skill_loader
            from core.tool_access_store import tool_access_store

            skills = skill_loader.get_skills_summary()
            if not skills:
                return ""

            runtime_role = self._runtime_role(runtime_user_id, platform)
            lines: List[str] = []
            for item in skills:
                name = str(item.get("name") or "").strip()
                desc = _short_desc(str(item.get("description") or ""), limit=60)
                if not name:
                    continue
                allowed_roles = _normalize_text_list(item.get("allowed_roles"))
                if allowed_roles and runtime_role not in allowed_roles:
                    continue
                allowed, _detail = tool_access_store.is_tool_allowed(
                    runtime_user_id=runtime_user_id,
                    platform=platform,
                    tool_name=f"ext_{name.replace('-', '_')}",
                    kind="tool",
                )
                if not allowed:
                    continue
                lines.append(f"- `{name}`: {desc}" if desc else f"- `{name}`")

            if not lines:
                return ""

            catalog = (
                "【可用技能目录】\n"
                "以下技能可供使用。当用户请求匹配某个技能时，**必须先调用 "
                '`load_skill(skill_name="xxx")` 获取完整操作指南（SOP）**，'
                "然后严格按照 SOP 中的步骤使用原子工具执行。\n"
                "**禁止在未加载 SOP 的情况下自行猜测执行方式。禁止load不在下面的列表中的技能**\n\n"
                + "\n".join(lines)
            )
            return catalog
        except Exception:
            return ""

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
                    platform="worker_kernel",
                    tool_name=tool_name,
                    kind="tool",
                )
                if allowed:
                    allowed_skills.append(skill_name)
            return allowed_skills
        except Exception:
            return []


prompt_composer = PromptComposer()
