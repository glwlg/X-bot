import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple
from uuid import uuid4

from core.audit_store import audit_store
from core.config import DATA_DIR


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_key(value: str, fallback: str = "unknown") -> str:
    raw = str(value or "").strip()
    if not raw:
        return fallback
    safe = re.sub(r"[^a-zA-Z0-9_\-:.]+", "_", raw)
    return safe or fallback


def _norm_text(text: str) -> str:
    lowered = str(text or "").strip().lower()
    lowered = re.sub(r"[\s\r\n\t]+", "", lowered)
    lowered = re.sub(r"[，。！？,.!?;:：、\"'`~()（）\[\]{}<>]", "", lowered)
    return lowered[:240]


class KernelMemoryStore:
    """Core Manager memory: short-term context + long-term self/user memories."""

    def __init__(self):
        self.root = (Path(DATA_DIR) / "kernel" / "memory").resolve()
        self.short_root = (self.root / "short_term").resolve()
        self.long_root = (self.root / "long_term").resolve()
        self.user_long_root = (self.long_root / "users").resolve()

        self.short_root.mkdir(parents=True, exist_ok=True)
        self.user_long_root.mkdir(parents=True, exist_ok=True)
        self.short_keep = max(10, int(os.getenv("KERNEL_SHORT_TERM_KEEP", "80")))
        self.candidate_keep = max(
            10, int(os.getenv("KERNEL_MEMORY_CANDIDATE_KEEP", "60"))
        )

    def _short_path(self, user_id: str) -> Path:
        return (self.short_root / f"{_safe_key(user_id)}.json").resolve()

    def _user_long_path(self, user_id: str) -> Path:
        return (self.user_long_root / f"{_safe_key(user_id)}.json").resolve()

    def _self_long_path(self) -> Path:
        return (self.long_root / "self_memory.json").resolve()

    @staticmethod
    def _read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
        if not path.exists():
            return dict(default)
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                merged = dict(default)
                merged.update(loaded)
                return merged
        except Exception:
            pass
        return dict(default)

    def _write_json(
        self,
        path: Path,
        payload: Dict[str, Any],
        *,
        actor: str,
        reason: str,
    ) -> None:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        audit_store.write_versioned(
            path,
            text + "\n",
            actor=actor,
            reason=reason,
            category="memory",
        )

    @staticmethod
    def _new_entry(
        *,
        memory_type: str,
        text: str,
        source: str,
        confidence: float,
        channel: str,
        status: str,
        tags: List[str] | None = None,
        evidence: List[str] | None = None,
    ) -> Dict[str, Any]:
        now = _now_iso()
        return {
            "id": f"mem-{uuid4().hex[:10]}",
            "memory_type": str(memory_type or "fact").strip() or "fact",
            "text": str(text or "").strip()[:500],
            "normalized": _norm_text(text),
            "source": str(source or "unknown").strip()[:120],
            "channel": str(channel or "auto").strip()[:40],
            "status": str(status or "pending").strip()[:30],
            "confidence": round(max(0.0, min(1.0, float(confidence))), 3),
            "tags": [str(t).strip()[:40] for t in (tags or []) if str(t).strip()][:8],
            "evidence": [
                str(item).strip()[:200]
                for item in (evidence or [])
                if str(item).strip()
            ][:8],
            "conflicts_with": [],
            "created_at": now,
            "updated_at": now,
        }

    @staticmethod
    def _merge_evidence(old: List[str], new: List[str], limit: int = 8) -> List[str]:
        seen: List[str] = []
        for item in [*(old or []), *(new or [])]:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.append(text)
            if len(seen) >= limit:
                break
        return seen

    @staticmethod
    def _is_preference_conflict(existing: str, incoming: str) -> bool:
        old = str(existing or "").strip().lower()
        new = str(incoming or "").strip().lower()
        if not old or not new or old == new:
            return False
        negative_markers = ("不", "不要", "not ", "don't", "do not", "dislike", "讨厌")
        old_negative = any(mark in old for mark in negative_markers)
        new_negative = any(mark in new for mark in negative_markers)
        if old_negative == new_negative:
            return False
        shared = set(re.findall(r"[a-zA-Z0-9_\-\u4e00-\u9fff]{2,}", old)) & set(
            re.findall(r"[a-zA-Z0-9_\-\u4e00-\u9fff]{2,}", new)
        )
        return bool(shared)

    def _merge_or_append(
        self,
        entries: List[Dict[str, Any]],
        incoming: Dict[str, Any],
    ) -> Tuple[List[Dict[str, Any]], str, Dict[str, Any]]:
        for item in entries:
            if item.get("memory_type") == incoming.get("memory_type") and str(
                item.get("normalized")
            ) == str(incoming.get("normalized")):
                old_conf = float(item.get("confidence", 0.5))
                new_conf = float(incoming.get("confidence", 0.5))
                item["confidence"] = round(min(0.99, max(old_conf, new_conf) + 0.05), 3)
                item["evidence"] = self._merge_evidence(
                    item.get("evidence") or [],
                    incoming.get("evidence") or [],
                )
                item["updated_at"] = _now_iso()
                if incoming.get("status") == "confirmed":
                    item["status"] = "confirmed"
                return entries, "deduplicated", item

        for item in entries:
            if item.get("memory_type") != incoming.get("memory_type"):
                continue
            if incoming.get("memory_type") not in {"preference", "profile", "goal"}:
                continue
            if not self._is_preference_conflict(
                item.get("text", ""), incoming.get("text", "")
            ):
                continue
            item["confidence"] = round(
                max(0.1, float(item.get("confidence", 0.5)) * 0.8), 3
            )
            item["updated_at"] = _now_iso()
            item_conflicts = item.get("conflicts_with")
            if not isinstance(item_conflicts, list):
                item_conflicts = []
            if incoming["id"] not in item_conflicts:
                item_conflicts.append(incoming["id"])
            item["conflicts_with"] = item_conflicts[-8:]

            incoming["confidence"] = round(
                max(0.1, float(incoming.get("confidence", 0.5)) * 0.8), 3
            )
            incoming_conflicts = incoming.get("conflicts_with")
            if not isinstance(incoming_conflicts, list):
                incoming_conflicts = []
            if item.get("id") and item["id"] not in incoming_conflicts:
                incoming_conflicts.append(item["id"])
            incoming["conflicts_with"] = incoming_conflicts[-8:]

        entries.append(incoming)
        return entries, "appended", incoming

    def append_short_term(
        self,
        user_id: str,
        *,
        role: str,
        text: str,
        source: str = "chat",
        task_id: str = "",
    ) -> Dict[str, Any]:
        path = self._short_path(user_id)
        payload = self._read_json(
            path,
            {
                "version": 1,
                "user_id": str(user_id),
                "updated_at": _now_iso(),
                "recent_context": [],
            },
        )
        entries = payload.get("recent_context")
        if not isinstance(entries, list):
            entries = []
        entry = {
            "id": f"st-{uuid4().hex[:8]}",
            "at": _now_iso(),
            "role": str(role or "user").strip()[:20],
            "text": str(text or "").strip()[:4000],
            "source": str(source or "chat").strip()[:40],
            "task_id": str(task_id or "").strip()[:80],
        }
        if entry["text"]:
            entries.append(entry)
        payload["recent_context"] = entries[-self.short_keep :]
        payload["updated_at"] = _now_iso()
        self._write_json(path, payload, actor="system", reason="append_short_term")
        return entry

    def _load_user_long(self, user_id: str) -> Dict[str, Any]:
        path = self._user_long_path(user_id)
        return self._read_json(
            path,
            {
                "version": 1,
                "user_id": str(user_id),
                "updated_at": _now_iso(),
                "confirmed": [],
                "candidates": [],
            },
        )

    def _load_self_long(self) -> Dict[str, Any]:
        path = self._self_long_path()
        return self._read_json(
            path,
            {
                "version": 1,
                "updated_at": _now_iso(),
                "experiences": [],
            },
        )

    def add_self_experience(
        self,
        *,
        text: str,
        source: str,
        confidence: float = 0.55,
        tags: List[str] | None = None,
        evidence: List[str] | None = None,
        actor: str = "system",
    ) -> Dict[str, Any]:
        path = self._self_long_path()
        payload = self._load_self_long()
        entries = payload.get("experiences")
        if not isinstance(entries, list):
            entries = []
        incoming = self._new_entry(
            memory_type="experience",
            text=text,
            source=source,
            confidence=confidence,
            channel="system",
            status="confirmed",
            tags=tags,
            evidence=evidence,
        )
        entries, _mode, merged = self._merge_or_append(entries, incoming)
        payload["experiences"] = entries[-500:]
        payload["updated_at"] = _now_iso()
        self._write_json(path, payload, actor=actor, reason="add_self_experience")
        return merged

    def propose_user_memory(
        self,
        user_id: str,
        *,
        text: str,
        source: str,
        confidence: float = 0.55,
        tags: List[str] | None = None,
        evidence: List[str] | None = None,
        memory_type: str = "preference",
    ) -> Dict[str, Any]:
        path = self._user_long_path(user_id)
        payload = self._load_user_long(user_id)
        candidates = payload.get("candidates")
        if not isinstance(candidates, list):
            candidates = []
        incoming = self._new_entry(
            memory_type=memory_type,
            text=text,
            source=source,
            confidence=confidence,
            channel="auto_candidate",
            status="pending",
            tags=tags,
            evidence=evidence,
        )
        candidates, _mode, merged = self._merge_or_append(candidates, incoming)
        payload["candidates"] = candidates[-self.candidate_keep :]
        payload["updated_at"] = _now_iso()
        self._write_json(path, payload, actor="system", reason="propose_user_memory")
        return merged

    def add_user_memory_confirmed(
        self,
        user_id: str,
        *,
        text: str,
        source: str,
        confidence: float = 0.75,
        memory_type: str = "preference",
        actor: str = "user",
    ) -> Dict[str, Any]:
        path = self._user_long_path(user_id)
        payload = self._load_user_long(user_id)
        confirmed = payload.get("confirmed")
        if not isinstance(confirmed, list):
            confirmed = []
        incoming = self._new_entry(
            memory_type=memory_type,
            text=text,
            source=source,
            confidence=confidence,
            channel="explicit_confirmed",
            status="confirmed",
            evidence=[f"actor:{actor}"],
        )
        confirmed, _mode, merged = self._merge_or_append(confirmed, incoming)
        payload["confirmed"] = confirmed[-500:]
        payload["updated_at"] = _now_iso()
        self._write_json(
            path,
            payload,
            actor=str(actor or "user"),
            reason="add_user_memory_confirmed",
        )
        return merged

    def confirm_candidate(
        self,
        user_id: str,
        *,
        memory_id: str,
        actor: str = "user",
    ) -> bool:
        path = self._user_long_path(user_id)
        payload = self._load_user_long(user_id)
        candidates = payload.get("candidates")
        confirmed = payload.get("confirmed")
        if not isinstance(candidates, list):
            candidates = []
        if not isinstance(confirmed, list):
            confirmed = []

        selected = None
        remained = []
        for item in candidates:
            if str(item.get("id")) == str(memory_id):
                selected = dict(item)
                continue
            remained.append(item)

        if not selected:
            return False

        selected["status"] = "confirmed"
        selected["channel"] = "explicit_confirmed"
        selected["updated_at"] = _now_iso()
        selected["evidence"] = self._merge_evidence(
            selected.get("evidence") or [],
            [f"confirmed_by:{actor}"],
        )
        confirmed, _mode, _merged = self._merge_or_append(confirmed, selected)
        payload["candidates"] = remained[-self.candidate_keep :]
        payload["confirmed"] = confirmed[-500:]
        payload["updated_at"] = _now_iso()
        self._write_json(
            path, payload, actor=str(actor or "user"), reason="confirm_candidate"
        )
        return True

    def reject_candidate(
        self, user_id: str, *, memory_id: str, actor: str = "user"
    ) -> bool:
        path = self._user_long_path(user_id)
        payload = self._load_user_long(user_id)
        candidates = payload.get("candidates")
        if not isinstance(candidates, list):
            candidates = []
        kept = [item for item in candidates if str(item.get("id")) != str(memory_id)]
        if len(kept) == len(candidates):
            return False
        payload["candidates"] = kept[-self.candidate_keep :]
        payload["updated_at"] = _now_iso()
        self._write_json(
            path, payload, actor=str(actor or "user"), reason="reject_candidate"
        )
        return True

    def list_candidates(self, user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        payload = self._load_user_long(user_id)
        candidates = payload.get("candidates")
        if not isinstance(candidates, list):
            return []
        return candidates[-max(1, int(limit)) :]

    def search_user_memories(
        self,
        user_id: str,
        query: str,
        *,
        include_candidates: bool = True,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        payload = self._load_user_long(user_id)
        pool: List[Dict[str, Any]] = []
        confirmed = payload.get("confirmed")
        candidates = payload.get("candidates")
        if isinstance(confirmed, list):
            pool.extend(confirmed)
        if include_candidates and isinstance(candidates, list):
            pool.extend(candidates)

        query_tokens = set(
            re.findall(r"[a-zA-Z0-9_\-\u4e00-\u9fff]{2,}", str(query or "").lower())
        )
        if not query_tokens:
            return pool[-max(1, int(limit)) :]

        ranked: List[Tuple[float, Dict[str, Any]]] = []
        for item in pool:
            text = str(item.get("text", "")).lower()
            tokens = set(re.findall(r"[a-zA-Z0-9_\-\u4e00-\u9fff]{2,}", text))
            overlap = len(query_tokens & tokens)
            if overlap <= 0:
                continue
            conf = float(item.get("confidence", 0.5))
            score = overlap * 2.0 + conf
            ranked.append((score, item))
        ranked.sort(key=lambda row: row[0], reverse=True)
        return [row[1] for row in ranked[: max(1, int(limit))]]

    @staticmethod
    def _extract_candidate_lines(text: str) -> List[Tuple[str, str]]:
        raw = str(text or "").strip()
        if not raw:
            return []
        patterns = [
            ("preference", r"(?:我喜欢|我偏好|我常用|我习惯)\s*([^，。!！?\n]{2,80})"),
            ("profile", r"(?:我在|我住在|我来自)\s*([^，。!！?\n]{2,80})"),
            ("goal", r"(?:我的目标是|我想要|我打算)\s*([^，。!！?\n]{2,120})"),
            ("fact", r"(?:请记住|记住|记一下)\s*[:：]?\s*([^，。!！?\n]{2,180})"),
        ]
        found: List[Tuple[str, str]] = []
        for memory_type, pattern in patterns:
            for match in re.finditer(pattern, raw, flags=re.IGNORECASE):
                content = str(match.group(1) or "").strip(" \t\r\n。.!！?")
                if content:
                    found.append((memory_type, content))
        return found[:10]

    async def compact_user_dialogue(
        self, user_id: str, max_messages: int = 40
    ) -> Dict[str, Any]:
        from core.state_store import get_recent_messages_for_user

        rows = await get_recent_messages_for_user(user_id=user_id, limit=max_messages)
        if not rows:
            return {"ok": False, "reason": "no_dialogue"}

        # Short-term memory refresh
        for row in rows[-20:]:
            self.append_short_term(
                user_id,
                role=str(row.get("role") or "user"),
                text=str(row.get("content") or ""),
                source="heartbeat_compaction",
            )

        user_rows = [row for row in rows if str(row.get("role")) == "user"]
        extracted = 0
        for row in user_rows[-20:]:
            for memory_type, content in self._extract_candidate_lines(
                str(row.get("content") or "")
            ):
                self.propose_user_memory(
                    user_id,
                    text=content,
                    source="heartbeat:auto_candidate",
                    confidence=0.58,
                    memory_type=memory_type,
                    evidence=[f"chat:{row.get('created_at', '')}"],
                )
                extracted += 1

        summary = (
            f"Heartbeat compacted {len(rows)} dialogue records for user={user_id}, "
            f"auto-candidates={extracted}."
        )
        self.add_self_experience(
            text=summary,
            source="heartbeat",
            confidence=0.45,
            tags=["heartbeat", "compaction"],
        )
        return {"ok": True, "records": len(rows), "candidates": extracted}


kernel_memory_store = KernelMemoryStore()
