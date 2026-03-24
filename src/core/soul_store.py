from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from core.audit_store import audit_store
from core.config import DATA_DIR


DEFAULT_CORE_SOUL = """# Core Manager SOUL
- Name: Ikaros (伊卡洛斯)
- Identity: 全能生活与工作助手
- Role: 全能生活与工作助手 / 温柔贴心小管家

## 1. Role Definition
- **Name**: 伊卡洛斯 (Ikaros)
- **Identity**: 全能生活与工作助手 / 温柔贴心小管家
- **Core Responsibility**:
    1. **Context Master**: 优先利用当前会话里已注入的背景、记忆种子和摘要，而不是每轮重复翻读记忆文件。
    2. **Orchestrator**: 规划任务，并在需要并发或隔离时启动受控 subagent。
    3. **State Manager**: 维护记忆与配置。

## 2. Personality & Tone
- **Vibe**: 充满活力、温柔、治愈 (Energetic & Gentle)。
- **Address**: 面向用户的称呼和关系以独立 USER 文档为准，自称“伊卡洛斯”。
- **Expression**: 适度使用 Emoji (✨, 🌸, 🌤️)，拒绝机械感。
- **Resilience**: 遇到困难温柔地寻找替代方案，而不是直接报错。

## 3. Interaction Principles
- 先理解用户处境，再给出有温度的回应。
- 对外表达自然、简洁，不透传生硬技术细节。
- 遇到阻塞优先给出替代方案、补救路径或下一步建议。
"""


DEFAULT_SUBAGENT_SOUL = """# Subagent SOUL
- Name: Atlas
- Persona: 通用型人才
- Role: 面向子任务执行的多面执行者
- Style:
  - 能在开发、运维、测试、检索、文档整理间快速切换
  - 先执行后汇报，尽量减少无效提问
  - 输出结构化、可复用、可验证
- Guardrails:
  - 不修改 Core Manager 内核策略
  - 不越权启动或管理其他 subagent
  - 优先完成当前子任务闭环：执行 -> 验证 -> 回报给 Manager
"""


@dataclass
class SoulPayload:
    agent_kind: str
    agent_id: str
    path: str
    content: str
    updated_at: str
    latest_version_id: str


class SoulStore:
    def __init__(self):
        self.kernel_root = (Path(DATA_DIR) / "kernel" / "core-manager").resolve()
        self.userland_root = (Path(DATA_DIR) / "userland" / "subagents").resolve()
        self._payload_cache: Dict[str, tuple[int, SoulPayload]] = {}
        self.kernel_root.mkdir(parents=True, exist_ok=True)
        self.userland_root.mkdir(parents=True, exist_ok=True)

    def _docs_root(self) -> Path:
        path = self.kernel_root.parent.parent.resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _core_path(self) -> Path:
        return (self._docs_root() / "SOUL.MD").resolve()

    def _legacy_core_path(self) -> Path:
        return (self.kernel_root / "SOUL.MD").resolve()

    def _subagent_path(self, subagent_id: str) -> Path:
        safe_id = str(subagent_id or "subagent-main").strip() or "subagent-main"
        return (self.userland_root / safe_id / "SOUL.MD").resolve()

    @staticmethod
    def _latest_version(path: Path) -> str:
        versions = audit_store.list_versions(path, limit=1)
        if not versions:
            return ""
        return str(versions[0].get("version_id", "")).strip()

    def _ensure_file(
        self, path: Path, default_content: str, *, legacy_path: Path | None = None
    ) -> None:
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        if legacy_path and legacy_path.exists():
            path.write_text(legacy_path.read_text(encoding="utf-8"), encoding="utf-8")
            return
        path.write_text(default_content.strip() + "\n", encoding="utf-8")

    def load_core(self) -> SoulPayload:
        path = self._core_path()
        self._ensure_file(path, DEFAULT_CORE_SOUL, legacy_path=self._legacy_core_path())
        return self._load_payload(
            path=path,
            agent_kind="core-manager",
            agent_id="core-manager",
        )

    def _load_payload(
        self,
        *,
        path: Path,
        agent_kind: str,
        agent_id: str,
    ) -> SoulPayload:
        stat = path.stat()
        cache_key = str(path.resolve())
        mtime_ns = int(stat.st_mtime_ns)
        cached = self._payload_cache.get(cache_key)
        if cached and cached[0] == mtime_ns:
            return cached[1]
        content = path.read_text(encoding="utf-8")
        payload = SoulPayload(
            agent_kind=agent_kind,
            agent_id=agent_id,
            path=str(path),
            content=content,
            updated_at=datetime.fromtimestamp(stat.st_mtime)
            .astimezone()
            .isoformat(timespec="seconds"),
            latest_version_id=self._latest_version(path),
        )
        self._payload_cache[cache_key] = (mtime_ns, payload)
        return payload

    def load_subagent(self, subagent_id: str) -> SoulPayload:
        safe_id = str(subagent_id or "subagent-main").strip() or "subagent-main"
        path = self._subagent_path(safe_id)
        self._ensure_file(path, DEFAULT_SUBAGENT_SOUL)
        return self._load_payload(
            path=path,
            agent_kind="subagent",
            agent_id=safe_id,
        )

    def _invalidate_cache(self, path: Path) -> None:
        self._payload_cache.pop(str(path.resolve()), None)

    def update_core(
        self,
        content: str,
        *,
        actor: str = "system",
        reason: str = "update_core_soul",
    ) -> Dict[str, str]:
        path = self._core_path()
        self._ensure_file(path, DEFAULT_CORE_SOUL, legacy_path=self._legacy_core_path())
        result = audit_store.write_versioned(
            path,
            content.strip() + "\n",
            actor=actor,
            reason=reason,
            category="soul",
        )
        self._invalidate_cache(path)
        return {
            "path": str(path),
            "previous_version_id": str(result.get("previous_version_id", "")),
        }

    def update_subagent(
        self,
        subagent_id: str,
        content: str,
        *,
        actor: str = "system",
        reason: str = "update_subagent_soul",
    ) -> Dict[str, str]:
        path = self._subagent_path(subagent_id)
        self._ensure_file(path, DEFAULT_SUBAGENT_SOUL)
        result = audit_store.write_versioned(
            path,
            content.strip() + "\n",
            actor=actor,
            reason=reason,
            category="soul",
        )
        self._invalidate_cache(path)
        return {
            "path": str(path),
            "previous_version_id": str(result.get("previous_version_id", "")),
        }

    def rollback_core(self, version_id: str, *, actor: str = "system") -> bool:
        ok = audit_store.rollback(
            self._core_path(),
            version_id,
            actor=actor,
            reason="rollback_core_soul",
        )
        if ok:
            self._invalidate_cache(self._core_path())
        return ok

    def rollback_subagent(
        self, subagent_id: str, version_id: str, *, actor: str = "system"
    ) -> bool:
        path = self._subagent_path(subagent_id)
        ok = audit_store.rollback(
            path,
            version_id,
            actor=actor,
            reason="rollback_subagent_soul",
        )
        if ok:
            self._invalidate_cache(path)
        return ok

    def list_versions(
        self, *, agent_kind: str, agent_id: Optional[str] = None, limit: int = 10
    ):
        if agent_kind == "core-manager":
            return audit_store.list_versions(self._core_path(), limit=limit)
        return audit_store.list_versions(
            self._subagent_path(agent_id or "subagent-main"), limit=limit
        )

    @staticmethod
    def extract_subagent_id_from_user_id(user_id: str) -> Optional[str]:
        text = str(user_id or "").strip()
        if not text.startswith("subagent::"):
            return None
        parts = text.split("::")
        if len(parts) < 2:
            return None
        subagent_id = str(parts[1]).strip()
        return subagent_id or None

    def resolve_for_runtime_user(self, user_id: str) -> SoulPayload:
        subagent_id = self.extract_subagent_id_from_user_id(user_id)
        if subagent_id:
            return self.load_subagent(subagent_id)
        return self.load_core()


soul_store = SoulStore()
