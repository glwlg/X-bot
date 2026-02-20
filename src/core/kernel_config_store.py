import json
from pathlib import Path
from typing import Any, Dict

from core.audit_store import audit_store
from core.config import DATA_DIR


class KernelConfigStore:
    """Versioned core runtime configuration snapshot."""

    def __init__(self):
        self.path = (Path(DATA_DIR) / "kernel" / "core-config.json").resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def snapshot(self, payload: Dict[str, Any], *, actor: str = "system", reason: str = "snapshot") -> None:
        normalized = {
            "version": 1,
            "config": payload or {},
        }
        text = json.dumps(normalized, ensure_ascii=False, indent=2) + "\n"
        audit_store.write_versioned(
            self.path,
            text,
            actor=actor,
            reason=reason,
            category="config",
        )

    def read(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "config": {}}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                return loaded
        except Exception:
            pass
        return {"version": 1, "config": {}}

    def rollback(self, version_id: str, *, actor: str = "system") -> bool:
        return audit_store.rollback(
            self.path,
            version_id,
            actor=actor,
            reason="rollback_core_config",
        )


kernel_config_store = KernelConfigStore()
