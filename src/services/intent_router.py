"""Intent routing utilities."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from core.config import ROUTING_MODEL, gemini_client

logger = logging.getLogger(__name__)


@dataclass
class IntentDecision:
    intent: str
    confidence: float
    reason: str
    raw: str = ""


@dataclass
class DispatchDecision:
    route: str
    confidence: float
    reason: str
    raw: str = ""


class IntentRouter:
    """Simplified router - LLM decides everything in the main loop."""

    def __init__(self):
        self.model = ROUTING_MODEL

    async def classify(self, message: str) -> IntentDecision:
        routed = await self.route(message)
        route = str(routed.route or "").strip().lower()
        intent = "task" if route == "worker_task" else "chat"
        return IntentDecision(
            intent=intent,
            confidence=float(routed.confidence),
            reason=str(routed.reason or ""),
            raw=str(routed.raw or ""),
        )

    async def route(self, message: str) -> DispatchDecision:
        text = str(message or "").strip()
        if not text:
            return DispatchDecision(
                route="manager_chat", confidence=0.0, reason="empty_message"
            )

        prompt = (
            "你是任务派发路由器。判断用户消息是否需要派发给 Worker 执行。\n"
            "- worker_task: 需要执行命令、搜索、研究、多步骤完成的任务\n"
            "- manager_chat: 闲聊、简单问答、问候\n"
            "如果不确定，默认选择 worker_task。\n"
            '返回 JSON 格式：{"route": "worker_task" 或 "manager_chat", "confidence": 0-1, "reason": "原因"}\n'
            f"用户消息: {text}"
        )
        try:
            response = await gemini_client.aio.models.generate_content(
                model=self.model,
                contents=prompt,
                config={"temperature": 0},
            )
            payload = str(response.text or "").strip()
            parsed = self._parse_json(payload)
            route = str(parsed.get("route", "worker_task")).strip().lower()
            if route not in {"worker_task", "manager_chat"}:
                route = "worker_task"
            confidence = parsed.get("confidence", 0.0)
            try:
                conf = max(0.0, min(1.0, float(confidence)))
            except Exception:
                conf = 0.0
            reason = str(parsed.get("reason", "") or "").strip()[:200]
            return DispatchDecision(
                route=route, confidence=conf, reason=reason, raw=payload[:400]
            )
        except Exception as exc:
            logger.debug("IntentRouter classify failed: %s", exc, exc_info=True)
            return DispatchDecision(
                route="worker_task",
                confidence=0.0,
                reason=f"classifier_error:{exc}",
            )

    @staticmethod
    def _parse_json(raw: str) -> dict:
        text = str(raw or "").strip()
        if not text:
            return {}
        try:
            loaded = json.loads(text)
            if isinstance(loaded, dict):
                return loaded
        except Exception:
            pass
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return {}
        try:
            loaded = json.loads(match.group(0))
            if isinstance(loaded, dict):
                return loaded
        except Exception:
            return {}
        return {}


intent_router = IntentRouter()
