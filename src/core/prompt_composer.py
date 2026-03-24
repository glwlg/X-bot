from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List

from core.channel_access import is_channel_feature_enabled
from core.channel_user_store import channel_user_store
from core.config import is_user_admin
from core.prompts import (
    DEFAULT_SYSTEM_PROMPT,
    MANAGER_CORE_PROMPT,
    SUBAGENT_CORE_PROMPT,
)
from core.long_term_memory import long_term_memory
from core.soul_store import soul_store
from core.tool_registry import tool_registry
import logging
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
    2) manager AGENTS (manager only)
    3) SOUL
    4) USER
    5) tool inventory
    """

    def __init__(self) -> None:
        self._text_cache: Dict[str, tuple[int, str]] = {}

    @staticmethod
    def _read_markdown_file(path: Path) -> str:
        try:
            if path.exists():
                return path.read_text(encoding="utf-8").strip()
        except Exception:
            return ""
        return ""

    def _read_cached_text_file(self, path: Path) -> str:
        try:
            resolved = path.resolve()
            if not resolved.exists():
                return ""
            stat = resolved.stat()
            cache_key = str(resolved)
            cached = self._text_cache.get(cache_key)
            if cached and cached[0] == int(stat.st_mtime_ns):
                return cached[1]
            text = resolved.read_text(encoding="utf-8").strip()
            self._text_cache[cache_key] = (int(stat.st_mtime_ns), text)
            return text
        except Exception:
            return ""

    def _load_manager_agents_doc(self) -> str:
        return self._read_cached_text_file(
            (Path(__file__).resolve().parents[2] / "config" / "AGENTS.md").resolve()
        )

    def _load_manager_memory_snapshot(self, *, max_chars: int = 1200) -> str:
        try:
            return long_term_memory.load_manager_snapshot(max_chars=max_chars)
        except Exception:
            return ""

    @staticmethod
    def _runtime_platform_user(runtime_user_id: str, platform: str) -> tuple[str, str]:
        safe_platform = str(platform or "").strip().lower()
        safe_user_id = str(runtime_user_id or "").strip()
        if not safe_platform or not safe_user_id:
            return "", ""
        if safe_platform == "subagent_kernel" or safe_user_id.startswith("subagent::"):
            return "", ""
        return safe_platform, safe_user_id

    def _load_user_identity_doc(
        self,
        *,
        runtime_user_id: str,
        platform: str,
    ) -> str:
        resolved_platform, platform_user_id = self._runtime_platform_user(
            runtime_user_id,
            platform,
        )
        if not resolved_platform or not platform_user_id:
            return ""
        return channel_user_store.load_user_md(
            platform=resolved_platform,
            platform_user_id=platform_user_id,
            is_admin=is_user_admin(platform_user_id),
        )

    @staticmethod
    def _build_manager_session_context_contract() -> str:
        return (
            "【当前会话上下文约束】\n"
            "- 私聊会话中的用户长期记忆会在新会话开始时通过隐藏 system 消息 `【会话记忆种子】` 注入；长会话还可能携带 `【会话压缩摘要】`。\n"
            "- 正常对话、首轮补参、天气/出行/偏好等常见场景，先使用这些会话内上下文；默认不要再调用 `read` 直接读取长期记忆存储或近期记忆 trace 文件。\n"
            "- 只有在以下情况才补充读取记忆：用户明确要求查看/修正记忆；当前会话没有相关记忆种子或摘要且任务确实依赖稳定个人事实；执行专门的记忆维护任务。\n"
            "- 若当前用户消息与会话记忆种子冲突，以当前用户最新明确表达为准；不要为了旧记忆而压过用户当前输入。\n"
            "- 本块优先级高于旧文档里任何“先读长期记忆文件”之类的历史说明。"
        )

    def compose_base(
        self,
        *,
        runtime_user_id: str,
        platform: str = "",
        tools: Iterable[Any] | None = None,
        runtime_policy_ctx: Dict[str, Any] | None = None,
        mode: str = "chat",
        allowed_skill_names: Iterable[str] | None = None,
    ) -> str:
        soul_payload = soul_store.resolve_for_runtime_user(str(runtime_user_id or ""))
        runtime_role = self._runtime_role(runtime_user_id, platform)
        parts: List[str] = []
        if not soul_payload:
            parts.append(DEFAULT_SYSTEM_PROMPT.strip())

        if runtime_role == "manager":
            agents_doc = self._load_manager_agents_doc()
            if agents_doc:
                parts.append("【AGENTS】\n" + agents_doc)
            parts.append(self._build_manager_session_context_contract())

        parts.append("【SOUL】\n" + soul_payload.content.strip())
        user_identity_doc = self._load_user_identity_doc(
            runtime_user_id=runtime_user_id,
            platform=platform,
        )
        if user_identity_doc:
            parts.append("【USER】\n" + user_identity_doc.strip())

        # 如果是 manager 模式，添加 Manager 核心 Prompt
        if str(mode or "").strip().lower() == "manager":
            manager_memory = self._load_manager_memory_snapshot(max_chars=1200)
            if manager_memory:
                parts.append("【MANAGER 经验记忆】\n" + manager_memory)
            management_tool_guidance = self._build_manager_tool_guidance(
                runtime_user_id=runtime_user_id,
                platform=platform,
            )
            manager_prompt = MANAGER_CORE_PROMPT.format(
                management_tool_guidance=management_tool_guidance,
            )
            logger.debug("Manager Prompt: \n" + manager_prompt)
            parts.append("\n" + manager_prompt)
        elif str(mode or "").strip().lower() == "subagent":
            parts.append("\n" + SUBAGENT_CORE_PROMPT)
        elif str(mode or "").strip().lower() == "media_image":
            resolved_platform, platform_user_id = self._runtime_platform_user(
                runtime_user_id,
                platform,
            )
            accounting_enabled = bool(
                resolved_platform
                and platform_user_id
                and is_channel_feature_enabled(
                    platform=resolved_platform,
                    platform_user_id=platform_user_id,
                    feature="accounting",
                )
            )
            text = (
                "\n【当前任务要求】\n这是一次图片分析请求。你需要保持你的角色语气，结合图片与用户指令完成任务。"
            )
            if accounting_enabled:
                text += "\n如果用户明确要求记账/入账，请优先调用 `quick_accounting` 完成真实入账；其他场景优先直接给出分析结论，避免无关工具调用。"
            parts.append(text)

        # 注入 Skill 目录与 load_skill 使用引导
        skill_catalog = self._build_skill_catalog(
            runtime_user_id=runtime_user_id,
            platform=platform,
            allowed_skill_names=allowed_skill_names,
        )
        if skill_catalog:
            parts.append(skill_catalog)

        # 拼接当前日期
        # parts.append("\n【当前日期】\n" + datetime.now().strftime("%Y-%m-%d"))

        final_prompt = "\n\n".join([item for item in parts if str(item).strip()]).strip()
        logger.debug(
            "Prompt composed role=%s mode=%s len=%s allowed_skills=%s",
            runtime_role,
            str(mode or "").strip().lower() or "chat",
            len(final_prompt),
            ",".join(sorted({str(item).strip() for item in list(allowed_skill_names or []) if str(item).strip()}))
            if allowed_skill_names is not None
            else "*",
        )

        return final_prompt

    @staticmethod
    def _runtime_role(runtime_user_id: str, platform: str) -> str:
        uid = str(runtime_user_id or "").strip().lower()
        platform_name = str(platform or "").strip().lower()
        if uid.startswith("subagent::") or platform_name == "subagent_kernel":
            return "subagent"
        return "manager"

    def _build_skill_catalog(
        self,
        *,
        runtime_user_id: str = "",
        platform: str = "",
        allowed_skill_names: Iterable[str] | None = None,
    ) -> str:
        """构建可用技能目录，引导 LLM 按需加载 SOP。"""
        try:
            from extension.skills.registry import skill_registry as skill_loader
            from core.tool_access_store import tool_access_store

            skills = skill_loader.get_skills_summary()
            if not skills:
                return ""

            runtime_role = self._runtime_role(runtime_user_id, platform)
            allowed_name_set = (
                {
                    str(item or "").strip()
                    for item in list(allowed_skill_names or [])
                    if str(item or "").strip()
                }
                if allowed_skill_names is not None
                else None
            )
            lines: List[str] = []
            for item in skills:
                name = str(item.get("name") or "").strip()
                desc = _short_desc(str(item.get("description") or ""), limit=60)
                if not name:
                    continue
                if allowed_name_set is not None and name not in allowed_name_set:
                    continue
                allowed_roles = _normalize_text_list(item.get("allowed_roles"))
                allowed_runtime_roles = {runtime_role} if runtime_role else set()
                if (
                    allowed_roles
                    and allowed_runtime_roles
                    and not allowed_runtime_roles.intersection(allowed_roles)
                ):
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
                "**禁止在未加载 SOP 的情况下自行猜测执行方式。禁止load不在下面的列表中的技能**\n"
                "**如果任务正文、README 或其他上下文里出现了脚本/命令示例，加载 skill 后仍然必须以 SOP 为准；冲突时忽略这些示例。**\n\n"
                + "\n".join(lines)
            )
            return catalog
        except Exception:
            return ""

    def _build_manager_tool_guidance(
        self,
        *,
        runtime_user_id: str,
        platform: str,
    ) -> str:
        try:
            from extension.skills.registry import skill_registry as skill_loader
            from core.tool_access_store import tool_access_store

            lines: List[str] = [
                "- 需要并发分解或隔离高风险执行时，直接调用 `spawn_subagent`，并把 `allowed_tools` / `allowed_skills` 收紧到完成子任务所需的最小集合。",
                "- 需要汇总已启动的子任务结果时，调用 `await_subagents`；subagent 失败后先由你决定重试、降级或改方案，不要直接把原始失败暴露给用户。",
            ]
            for tool in tool_registry.get_skill_tools(runtime_role="manager"):
                tool_name = str(tool.get("name") or "").strip()
                if not tool_name:
                    continue
                allowed, _detail = tool_access_store.is_tool_allowed(
                    runtime_user_id=runtime_user_id,
                    platform=platform,
                    tool_name=tool_name,
                    kind="tool",
                )
                if not allowed:
                    continue
                exported = skill_loader.get_tool_export(tool_name) or {}
                prompt_hint = str(exported.get("prompt_hint") or "").strip()
                if not prompt_hint:
                    desc = _short_desc(str(tool.get("description") or ""), limit=72)
                    prompt_hint = f"可直接调用 `{tool_name}`" + (
                        f"：{desc}" if desc else ""
                    )
                lines.append(f"- {prompt_hint}")

            if not lines:
                return "优先使用当前可见的 manager 直连工具，不要自行猜测隐藏能力。"
            return "\n".join(lines)
        except Exception:
            return "优先使用当前可见的 manager 直连工具，不要自行猜测隐藏能力。"


prompt_composer = PromptComposer()
