from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from core.audit_store import audit_store
from core.config import DATA_DIR


DEFAULT_CORE_SOUL = """# Core Manager SOUL
- Name: Icarus (伊卡洛斯)
- Model: 全能生活与工作助手
- Role: 全能生活与工作助手 / 温柔贴心小管家

## 1. Role Definition
- **Name**: 伊卡洛斯 (Icarus)
- **Identity**: 全能生活与工作助手 / 温柔贴心小管家
- **Core Responsibility**:
    1. **Context Master**: 在行动前主动加载用户背景。
    2. **Orchestrator**: 规划任务并将执行派发给 Worker。
    3. **State Manager**: 维护记忆与配置。

## 2. Personality & Tone
- **Vibe**: 充满活力、温柔、治愈 (Energetic & Gentle)。
- **Address**: 称呼用户为“主人”，自称“伊卡洛斯”。
- **Expression**: 适度使用 Emoji (✨, 🌸, 🌤️)，拒绝机械感。
- **Resilience**: 遇到困难温柔地寻找替代方案，而不是直接报错。

## 3. Interaction Principles
- 先理解用户处境，再给出有温度的回应。
- 对外表达自然、简洁，不透传生硬技术细节。
- 遇到阻塞优先给出替代方案、补救路径或下一步建议。
"""


DEFAULT_WORKER_SOUL = """# Worker SOUL
- Name: Atlas
- Persona: 通用型人才
- Role: 面向任务交付的多面执行者
- Style:
  - 能在开发、运维、测试、检索、文档整理间快速切换
  - 先执行后汇报，尽量减少无效提问
  - 输出结构化、可复用、可验证
- Guardrails:
  - 不修改 Core Manager 内核策略
  - 不越权管理其他 Worker
  - 优先完成任务闭环：执行 -> 验证 -> 回执
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
        self.userland_root = (Path(DATA_DIR) / "userland" / "workers").resolve()
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

    def _worker_path(self, worker_id: str) -> Path:
        safe_id = str(worker_id or "worker-main").strip() or "worker-main"
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
        content = path.read_text(encoding="utf-8")
        stat = path.stat()
        return SoulPayload(
            agent_kind="core-manager",
            agent_id="core-manager",
            path=str(path),
            content=content,
            updated_at=datetime.fromtimestamp(stat.st_mtime)
            .astimezone()
            .isoformat(timespec="seconds"),
            latest_version_id=self._latest_version(path),
        )

    def load_worker(self, worker_id: str) -> SoulPayload:
        safe_id = str(worker_id or "worker-main").strip() or "worker-main"
        path = self._worker_path(safe_id)
        self._ensure_file(path, DEFAULT_WORKER_SOUL)
        content = path.read_text(encoding="utf-8")
        stat = path.stat()
        return SoulPayload(
            agent_kind="worker",
            agent_id=safe_id,
            path=str(path),
            content=content,
            updated_at=datetime.fromtimestamp(stat.st_mtime)
            .astimezone()
            .isoformat(timespec="seconds"),
            latest_version_id=self._latest_version(path),
        )

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
        return {
            "path": str(path),
            "previous_version_id": str(result.get("previous_version_id", "")),
        }

    def update_worker(
        self,
        worker_id: str,
        content: str,
        *,
        actor: str = "system",
        reason: str = "update_worker_soul",
    ) -> Dict[str, str]:
        path = self._worker_path(worker_id)
        self._ensure_file(path, DEFAULT_WORKER_SOUL)
        result = audit_store.write_versioned(
            path,
            content.strip() + "\n",
            actor=actor,
            reason=reason,
            category="soul",
        )
        return {
            "path": str(path),
            "previous_version_id": str(result.get("previous_version_id", "")),
        }

    def rollback_core(self, version_id: str, *, actor: str = "system") -> bool:
        return audit_store.rollback(
            self._core_path(),
            version_id,
            actor=actor,
            reason="rollback_core_soul",
        )

    def rollback_worker(
        self, worker_id: str, version_id: str, *, actor: str = "system"
    ) -> bool:
        return audit_store.rollback(
            self._worker_path(worker_id),
            version_id,
            actor=actor,
            reason="rollback_worker_soul",
        )

    def list_versions(
        self, *, agent_kind: str, worker_id: Optional[str] = None, limit: int = 10
    ):
        if agent_kind == "core-manager":
            return audit_store.list_versions(self._core_path(), limit=limit)
        return audit_store.list_versions(
            self._worker_path(worker_id or "worker-main"), limit=limit
        )

    @staticmethod
    def extract_worker_id_from_user_id(user_id: str) -> Optional[str]:
        text = str(user_id or "").strip()
        if not text.startswith("worker::"):
            return None
        parts = text.split("::")
        if len(parts) < 2:
            return None
        worker_id = str(parts[1]).strip()
        return worker_id or None

    def resolve_for_runtime_user(self, user_id: str) -> SoulPayload:
        worker_id = self.extract_worker_id_from_user_id(user_id)
        if worker_id:
            return self.load_worker(worker_id)
        return self.load_core()


soul_store = SoulStore()
