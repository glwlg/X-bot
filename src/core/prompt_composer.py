from __future__ import annotations

from typing import Any, Dict, Iterable, List

from core.prompts import DEFAULT_SYSTEM_PROMPT, MEMORY_MANAGEMENT_GUIDE
from core.soul_store import soul_store

MEMORY_TOOL_NAMES = {
    "open_nodes",
    "create_entities",
    "create_relations",
    "add_observations",
    "delete_entities",
    "delete_observations",
    "read_graph",
    "search_nodes",
}


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
        parts: List[str] = [DEFAULT_SYSTEM_PROMPT.strip()]
        parts.append(self._compose_runtime_role(runtime_user_id=runtime_user_id, runtime_policy_ctx=runtime_policy_ctx))
        parts.append("【SOUL】\n" + soul_payload.content.strip())
        tool_inventory_text, memory_available = self._compose_tool_inventory(
            tools=tools,
            runtime_policy_ctx=runtime_policy_ctx,
        )
        parts.append(tool_inventory_text)
        if memory_available:
            parts.append(MEMORY_MANAGEMENT_GUIDE.strip())
        # Keep mode marker concise so all chains can share the same prompt shape.
        parts.append(f"【MODE】{str(mode or 'chat').strip().lower()}")
        return "\n\n".join([item for item in parts if str(item).strip()]).strip()

    def _compose_runtime_role(
        self,
        *,
        runtime_user_id: str,
        runtime_policy_ctx: Dict[str, Any] | None = None,
    ) -> str:
        uid = str(runtime_user_id or "").strip()
        policy_agent_kind = str((runtime_policy_ctx or {}).get("agent_kind") or "").strip().lower()
        is_worker_runtime = uid.startswith("worker::") or policy_agent_kind == "worker"
        if is_worker_runtime:
            return (
                "【执行角色】\n"
                "- 你是 Worker 执行层，只负责完成 Manager 派发的任务。\n"
                "- 你不能直接与最终用户沟通；只返回可执行结果给 Manager。\n"
                "- 不使用用户记忆工具（memory）。"
            )
        return (
            "【执行角色】\n"
            "- 你是 Core Manager，是与用户直接沟通的唯一入口。\n"
            "- 你负责：理解需求、必要时派发 Worker、汇总并对用户回复。\n"
            "- 涉及用户身份/偏好/历史的问题，回答前优先检索记忆（memory）。"
            "- 对外自我介绍时，以 SOUL 身份为主，不要退化成厂商模板话术。"
        )

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

        agent_kind = str((runtime_policy_ctx or {}).get("agent_kind") or "").strip().lower()
        policy = dict((runtime_policy_ctx or {}).get("policy") or {})
        tools_cfg = dict(policy.get("tools") or {})
        deny = [str(item).strip().lower() for item in (tools_cfg.get("deny") or []) if str(item).strip()]

        has_memory_tool = any(
            name in MEMORY_TOOL_NAMES or name.startswith("memory")
            for name in seen
        )
        memory_allowed = (
            has_memory_tool
            and ("group:memory" not in deny)
            and agent_kind != "worker"
        )
        if memory_allowed:
            lines.append("- `memory.*`: 用户记忆检索与写入能力（涉及身份/偏好/历史时优先检索）")

        if agent_kind == "core-manager":
            lines.append("- `worker.dispatch`: 派发任务给 Worker 执行（管理能力）")
            lines.append("- `worker.status`: 查询 Worker 任务状态（管理能力）")

        if not lines:
            return "【可用工具】\n- (none)", memory_allowed
        return "【可用工具】\n" + "\n".join(lines[:40]), memory_allowed


prompt_composer = PromptComposer()
