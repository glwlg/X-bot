"""Unified request routing utilities."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable

from core.extension_router import ExtensionCandidate
from core.config import get_client_for_model
from core.model_config import (
    get_model_candidates_for_input,
    get_routing_model,
    mark_model_failed,
    mark_model_success,
)
from services.openai_adapter import generate_text

logger = logging.getLogger(__name__)

INTENT_ROUTER_TIMEOUT_SEC = 8.0


@dataclass
class RoutingDecision:
    request_mode: str
    candidate_skills: list[str]
    confidence: float
    reason: str
    task_tracking: bool | None = None
    raw: str = ""


def _normalize_skill_name(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    return raw.replace("-", "_")


def _render_dialog_window(messages: Iterable[dict[str, str]]) -> str:
    lines: list[str] = []
    for item in list(messages or [])[-10:]:
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        if role not in {"user", "assistant", "model"} or not content:
            continue
        label = "用户" if role == "user" else "助手"
        lines.append(f"{label}: {content}")
    return "\n".join(lines).strip()


def _render_skill_catalog(candidates: Iterable[ExtensionCandidate]) -> str:
    lines: list[str] = []
    for candidate in list(candidates or []):
        name = str(getattr(candidate, "name", "") or "").strip()
        if not name:
            continue
        description = str(getattr(candidate, "description", "") or "").strip()
        triggers = [
            str(item or "").strip()
            for item in list(getattr(candidate, "triggers", []) or [])[:8]
            if str(item or "").strip()
        ]
        line = f'- name: "{name}"'
        if description:
            line += f' | desc: "{description[:180]}"'
        if triggers:
            line += f' | triggers: "{", ".join(triggers[:6])}"'
        lines.append(line)
    return "\n".join(lines).strip()


class IntentRouter:
    """Route the current turn to chat/task mode and shrink skill scope."""

    async def classify(self, message: str) -> RoutingDecision:
        return await self.route(
            dialog_messages=[{"role": "user", "content": str(message or "").strip()}],
            candidates=[],
            max_candidates=0,
        )

    async def route(
        self,
        *,
        dialog_messages: Iterable[dict[str, str]],
        candidates: Iterable[ExtensionCandidate],
        max_candidates: int = 5,
    ) -> RoutingDecision:
        candidate_rows = [
            item for item in list(candidates or []) if getattr(item, "name", None)
        ]
        rendered_dialog = _render_dialog_window(dialog_messages)
        if not rendered_dialog:
            return RoutingDecision(
                request_mode="chat",
                candidate_skills=[],
                confidence=0.0,
                reason="empty_message",
                task_tracking=False,
            )

        rendered_catalog = _render_skill_catalog(candidate_rows)
        if not rendered_catalog:
            rendered_catalog = "无可用技能候选；candidate_skills 必须返回空数组。"

        prompt = (
            "你是统一请求路由器。请根据最近对话做两个判断：\n"
            "1. request_mode:\n"
            '- task: 本轮更像执行型请求，可能要用工具、检索、写代码、多步处理\n'
            '- chat: 闲聊、轻问答、互动小游戏、普通陪聊、一次性直接回答即可\n'
            "2. task_tracking:\n"
            "- true: 这个请求值得进入 `/task` 列表，需要跨轮跟踪、恢复、等待外部结果、后续回看或明确闭环\n"
            "- false: 一次性回答即可；即使会用工具/搜索，也不应进入 `/task`\n"
            "3. candidate_skills: 只从给定技能目录里选择本轮可能会用到的技能；如果没有明显相关技能，返回空数组。\n"
            "要求：\n"
            "- `task_tracking=true` 时，`request_mode` 必须是 `task`。\n"
            "- 普通寒暄、致谢、简短陪聊、一次性问答、一次性搜索、链接总结、仓库速览、普通解释，都应 `task_tracking=false`。\n"
            "- 只有明显需要后续跟进、可恢复执行、等待外部状态、持续推进的请求，才返回 `task_tracking=true`。\n"
            "- 如果不确定，默认返回 `request_mode=chat` 且 `task_tracking=false`。\n"
            "- candidate_skills 只能来自技能目录。\n"
            "- 倾向于宽松保留少量相关技能，但不要选明显无关的。\n"
            "- 只返回 JSON，不要输出解释文本。\n"
            'JSON 格式：{"request_mode":"task"|"chat","task_tracking":true|false,"candidate_skills":["skill_a"],"reason":"...","confidence":0-1}\n\n'
            f"最近对话：\n{rendered_dialog}\n\n"
            f"技能目录：\n{rendered_catalog}\n"
        )

        try:
            preferred_model = get_routing_model()
            candidate_models = get_model_candidates_for_input(
                input_type="text",
                pool_type="routing",
                preferred_model=preferred_model,
            )
            if not candidate_models and preferred_model:
                candidate_models = [preferred_model]
            if not candidate_models:
                raise RuntimeError("No candidate routing model available")

            first_attempt_model = candidate_models[0]
            last_error: Exception | None = None
            for index, model_name in enumerate(candidate_models):
                client = get_client_for_model(model_name, is_async=True)
                if client is None:
                    last_error = RuntimeError(
                        f"No async client available for routing model: {model_name}"
                    )
                    mark_model_failed(model_name)
                    next_model = (
                        candidate_models[index + 1]
                        if index + 1 < len(candidate_models)
                        else ""
                    )
                    if next_model:
                        logger.warning(
                            "[IntentRouter] Client unavailable for %s; trying %s",
                            model_name,
                            next_model,
                        )
                        continue
                    raise last_error

                try:
                    payload = await asyncio.wait_for(
                        generate_text(
                            async_client=client,
                            model=model_name,
                            contents=prompt,
                            config={
                                "system_instruction": (
                                    "你只负责本轮请求路由。"
                                    "不要回答用户问题，不要补充额外文本。"
                                ),
                                "temperature": 0,
                                "response_mime_type": "application/json",
                            },
                        ),
                        timeout=INTENT_ROUTER_TIMEOUT_SEC,
                    )
                    raw = str(payload or "").strip()
                    parsed = self._parse_json(raw)
                    mode = str(parsed.get("request_mode") or "").strip().lower()
                    if mode not in {"task", "chat"}:
                        mode = "chat"
                    reason = str(parsed.get("reason") or "").strip()[:240]
                    try:
                        confidence = max(
                            0.0, min(1.0, float(parsed.get("confidence") or 0.0))
                        )
                    except Exception:
                        confidence = 0.0
                    task_tracking = bool(parsed.get("task_tracking"))
                    if mode != "task":
                        task_tracking = False
                    selected = self._resolve_skills(
                        parsed.get("candidate_skills"),
                        candidate_rows,
                        max_candidates=max_candidates,
                    )
                    mark_model_success(model_name)
                    if model_name != first_attempt_model:
                        logger.warning(
                            "[IntentRouter] Routing failover succeeded: %s -> %s",
                            first_attempt_model,
                            model_name,
                        )
                    return RoutingDecision(
                        request_mode=mode,
                        candidate_skills=selected,
                        confidence=confidence,
                        reason=reason or "ok",
                        task_tracking=task_tracking,
                        raw=raw[:800],
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    last_error = exc
                    mark_model_failed(model_name)
                    next_model = (
                        candidate_models[index + 1]
                        if index + 1 < len(candidate_models)
                        else ""
                    )
                    if next_model:
                        logger.warning(
                            "[IntentRouter] Routing request failed via %s: %s; trying %s",
                            model_name,
                            exc,
                            next_model,
                        )
                        continue
                    raise

            if last_error is not None:
                raise last_error
            raise RuntimeError("No candidate routing model available")
        except Exception as exc:
            logger.debug("Intent router failed: %s", exc, exc_info=True)
            return RoutingDecision(
                request_mode="chat",
                candidate_skills=[],
                confidence=0.0,
                reason=f"router_error:{exc}",
                task_tracking=False,
            )

    @staticmethod
    def _resolve_skills(
        raw_skills: Any,
        candidates: list[ExtensionCandidate],
        *,
        max_candidates: int,
    ) -> list[str]:
        if not isinstance(raw_skills, list) or max_candidates <= 0:
            return []
        by_exact = {
            str(item.name or "").strip(): str(item.name or "").strip()
            for item in candidates
            if str(item.name or "").strip()
        }
        by_normalized = {
            _normalize_skill_name(str(item.name or "").strip()): str(item.name or "").strip()
            for item in candidates
            if str(item.name or "").strip()
        }
        selected: list[str] = []
        for item in raw_skills:
            token = str(item or "").strip()
            if not token:
                continue
            resolved = by_exact.get(token) or by_normalized.get(
                _normalize_skill_name(token)
            )
            if not resolved or resolved in selected:
                continue
            selected.append(resolved)
            if len(selected) >= max(1, int(max_candidates)):
                break
        return selected

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        text = str(raw or "").strip()
        if not text:
            raise ValueError("empty_response")
        try:
            loaded = json.loads(text)
            if isinstance(loaded, dict):
                return loaded
        except Exception:
            pass
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("json_not_found")
        loaded = json.loads(match.group(0))
        if not isinstance(loaded, dict):
            raise ValueError("json_not_object")
        return loaded


intent_router = IntentRouter()
