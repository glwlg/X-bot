from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from core.config import get_client_for_model
from core.model_config import get_current_model
from core.soul_store import SoulPayload, soul_store
from services.openai_adapter import generate_text

logger = logging.getLogger(__name__)

DEFAULT_WAITING_PHRASE_FILENAME = "WAITING_PHRASES.MD"
DEFAULT_REFRESH_THRESHOLD_SECONDS = 600

_RECEIVED_SECTION_KEYS = {
    "receivedphrases",
    "receivedphrase",
    "received",
    "收到词库",
    "收到短语",
    "接收词库",
    "接收提示",
}
_LOADING_SECTION_KEYS = {
    "loadingphrases",
    "loadingphrase",
    "loading",
    "加载词库",
    "加载短语",
    "思考词库",
    "等待提示",
}


def _normalize_heading_key(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[`*_~#\[\]\(\)<>]+", "", text)
    return re.sub(r"[\s_\-:：]+", "", text)


def _normalize_phrase_pool(values: Any, *, limit: int = 20) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        phrase = " ".join(str(raw or "").split()).strip().strip("`*-")
        if not phrase:
            continue
        if phrase in seen:
            continue
        seen.add(phrase)
        normalized.append(phrase)
        if len(normalized) >= max(1, int(limit)):
            break
    return normalized


class WaitingPhraseStore:
    def __init__(
        self, *, refresh_threshold_seconds: int = DEFAULT_REFRESH_THRESHOLD_SECONDS
    ):
        self.refresh_threshold_seconds = max(60, int(refresh_threshold_seconds))
        self._cache: dict[str, tuple[float, list[str], list[str]]] = {}
        self._startup_task: asyncio.Task | None = None

    @staticmethod
    def phrase_path_for_soul_payload(payload: SoulPayload) -> Path:
        soul_path = Path(str(payload.path)).resolve()
        return (soul_path.parent / DEFAULT_WAITING_PHRASE_FILENAME).resolve()

    def _should_refresh_for_soul(self, soul_path: Path, phrase_path: Path) -> bool:
        if not soul_path.exists():
            return False
        if not phrase_path.exists():
            return True
        try:
            soul_mtime = float(soul_path.stat().st_mtime)
            phrase_mtime = float(phrase_path.stat().st_mtime)
        except Exception:
            return True
        return (soul_mtime - phrase_mtime) > float(self.refresh_threshold_seconds)

    @staticmethod
    def _extract_json_object(raw_text: str) -> dict[str, Any] | None:
        text = str(raw_text or "").strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _extract_received_list(payload: dict[str, Any]) -> list[str]:
        for key in ("received_phrases", "receivedPhrases", "received", "received_list"):
            values = _normalize_phrase_pool(payload.get(key), limit=14)
            if values:
                return values
        return []

    @staticmethod
    def _extract_loading_list(payload: dict[str, Any]) -> list[str]:
        for key in ("loading_phrases", "loadingPhrases", "loading", "loading_list"):
            values = _normalize_phrase_pool(payload.get(key), limit=18)
            if values:
                return values
        return []

    @staticmethod
    def _render_markdown(
        *,
        received: list[str],
        loading: list[str],
        model: str,
        soul_path: str,
    ) -> str:
        lines = [
            "# Waiting Phrases",
            f"- model: {model}",
            f"- source_soul_path: {soul_path}",
            "",
            "## Received Phrases",
        ]
        lines.extend([f"- {item}" for item in received])
        lines.append("")
        lines.append("## Loading Phrases")
        lines.extend([f"- {item}" for item in loading])
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _parse_markdown(text: str) -> tuple[list[str], list[str]]:
        received: list[str] = []
        loading: list[str] = []
        section: str | None = None

        for raw in str(text or "").splitlines():
            line = raw.strip()
            if not line:
                continue

            heading_match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
            if heading_match:
                key = _normalize_heading_key(heading_match.group(1))
                if key in _RECEIVED_SECTION_KEYS:
                    section = "received"
                elif key in _LOADING_SECTION_KEYS:
                    section = "loading"
                else:
                    section = None
                continue

            key_match = re.match(r"^(?:[-*]\s*)?([^:：]+)\s*[:：]\s*$", line)
            if key_match:
                key = _normalize_heading_key(key_match.group(1))
                if key in _RECEIVED_SECTION_KEYS:
                    section = "received"
                elif key in _LOADING_SECTION_KEYS:
                    section = "loading"
                else:
                    section = None
                continue

            phrase_match = re.match(r"^[-*]\s+(.+?)\s*$", line)
            if not phrase_match or not section:
                continue
            phrase = str(phrase_match.group(1) or "").strip()
            if section == "received":
                received.append(phrase)
            elif section == "loading":
                loading.append(phrase)

        return _normalize_phrase_pool(received, limit=14), _normalize_phrase_pool(
            loading, limit=18
        )

    def load_phrase_pools(
        self, phrase_path: Path
    ) -> tuple[list[str], list[str]] | None:
        path = Path(phrase_path).resolve()
        if not path.exists():
            return None
        try:
            mtime = float(path.stat().st_mtime)
        except Exception:
            mtime = -1.0

        cache_key = str(path)
        cached = self._cache.get(cache_key)
        if cached and cached[0] == mtime:
            return list(cached[1]), list(cached[2])

        try:
            raw = path.read_text(encoding="utf-8")
        except Exception:
            return None

        received, loading = self._parse_markdown(raw)
        if not received and not loading:
            return None

        self._cache[cache_key] = (mtime, list(received), list(loading))
        return received, loading

    def load_phrase_pools_for_runtime_user(
        self, runtime_user_id: str
    ) -> tuple[list[str], list[str]] | None:
        payload = soul_store.resolve_for_runtime_user(str(runtime_user_id))
        phrase_path = self.phrase_path_for_soul_payload(payload)
        return self.load_phrase_pools(phrase_path)

    @staticmethod
    def _build_generation_prompt(payload: SoulPayload) -> str:
        soul_content = str(payload.content or "").strip()
        return (
            "你是一个文案生成器。请严格根据给定 SOUL 生成等待提示词。\n"
            "要求：\n"
            "1) 输出 JSON 对象，不要输出额外解释。\n"
            "2) 字段必须是 received_phrases 和 loading_phrases。\n"
            "3) received_phrases 生成 8-12 条，loading_phrases 生成 10-16 条。\n"
            "4) 语气和人格必须贴合 SOUL；每条保持自然、口语化、中文为主。\n"
            "5) 每条必须是一行短句，避免重复。\n\n"
            f"Agent Kind: {payload.agent_kind}\n"
            f"Agent ID: {payload.agent_id}\n\n"
            "SOUL 内容如下：\n"
            f"{soul_content}\n"
        )

    async def _generate_phrase_pools_with_llm(
        self, payload: SoulPayload
    ) -> tuple[list[str], list[str]] | None:
        model_to_use = get_current_model()
        client_to_use = get_client_for_model(model_to_use, is_async=True)
        if client_to_use is None:
            logger.warning("Skip waiting phrase generation: async client unavailable")
            return None
        prompt = self._build_generation_prompt(payload)
        try:
            raw = await generate_text(
                async_client=client_to_use,
                model=model_to_use,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "temperature": 0.9,
                    "max_output_tokens": 1200,
                },
            )
        except Exception as exc:
            logger.warning("Failed to generate waiting phrases with llm: %s", exc)
            return None

        payload_obj = self._extract_json_object(raw)
        if not payload_obj:
            logger.warning("Invalid waiting phrase generation response")
            return None

        received = self._extract_received_list(payload_obj)
        loading = self._extract_loading_list(payload_obj)
        if not received or not loading:
            logger.warning("Generated waiting phrases missing required sections")
            return None
        return received, loading

    async def refresh_if_needed_for_payload(self, payload: SoulPayload) -> bool:
        soul_path = Path(str(payload.path)).resolve()
        phrase_path = self.phrase_path_for_soul_payload(payload)
        if not self._should_refresh_for_soul(soul_path, phrase_path):
            return False

        generated = await self._generate_phrase_pools_with_llm(payload)
        if not generated:
            return False
        received, loading = generated

        try:
            phrase_path.parent.mkdir(parents=True, exist_ok=True)
            phrase_path.write_text(
                self._render_markdown(
                    received=received,
                    loading=loading,
                    model=get_current_model(),
                    soul_path=str(soul_path),
                ),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Failed writing waiting phrases markdown: %s", exc)
            return False

        self._cache.pop(str(phrase_path), None)
        logger.info("Updated waiting phrases from %s", soul_path)
        return True

    async def refresh_startup_if_needed(self) -> None:
        payloads = [soul_store.load_core(), soul_store.load_worker("worker-main")]
        results = await asyncio.gather(
            *(self.refresh_if_needed_for_payload(payload) for payload in payloads),
            return_exceptions=True,
        )
        for payload, result in zip(payloads, results):
            if isinstance(result, Exception):
                logger.warning(
                    "Waiting phrase refresh failed for %s/%s: %s",
                    payload.agent_kind,
                    payload.agent_id,
                    result,
                )

    def schedule_startup_refresh(self) -> None:
        if self._startup_task and not self._startup_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._startup_task = loop.create_task(
            self.refresh_startup_if_needed(),
            name="waiting-phrase-refresh",
        )

        def _on_done(task: asyncio.Task) -> None:
            try:
                task.result()
            except asyncio.CancelledError:
                logger.debug("waiting phrase refresh cancelled")
            except Exception as exc:
                logger.warning("waiting phrase refresh crashed: %s", exc)

        self._startup_task.add_done_callback(_on_done)


waiting_phrase_store = WaitingPhraseStore()
