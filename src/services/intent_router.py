"""Intent routing utilities."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from enum import Enum

from core.config import ROUTING_MODEL, gemini_client

logger = logging.getLogger(__name__)


class UserIntent(str, Enum):
    CHAT = "chat"
    GENERAL_CHAT = "general_chat"
    UNKNOWN = "unknown"
    DOWNLOAD_VIDEO = "download_video"
    GENERATE_IMAGE = "generate_image"
    SET_REMINDER = "set_reminder"


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
    """Model-based task/chat classifier with safe fallback."""

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
            return DispatchDecision(route="manager_chat", confidence=0.0, reason="empty_message")

        prompt = (
            "You are a dispatch router for a manager/worker assistant system.\n"
            "Decide one route for this user message:\n"
            "- worker_task: executable tasks, research/news lookups, coding, data processing, multi-step delivery\n"
            "- manager_memory: user-profile/memory questions (who am I, where I live, my preferences/history)\n"
            "- manager_chat: social small talk, acknowledgements, very short casual chat\n"
            "If uncertain, choose worker_task.\n"
            "Return JSON only with keys: route, confidence, reason.\n"
            "route must be one of: worker_task, manager_memory, manager_chat.\n"
            "confidence must be 0..1.\n"
            f"message: {text}"
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
            if route not in {"worker_task", "manager_memory", "manager_chat"}:
                route = "worker_task"
            confidence = parsed.get("confidence", 0.0)
            try:
                conf = max(0.0, min(1.0, float(confidence)))
            except Exception:
                conf = 0.0
            reason = str(parsed.get("reason", "") or "").strip()[:200]
            return DispatchDecision(route=route, confidence=conf, reason=reason, raw=payload[:400])
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
