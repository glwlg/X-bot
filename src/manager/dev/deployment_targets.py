from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _default_targets() -> Dict[str, Dict[str, str]]:
    return {
        "manager": {"service": "ikaros", "image": "ikaros-manager"},
        "api": {"service": "ikaros-api", "image": "ikaros-api"},
    }


class DeploymentTargets:
    def __init__(self, config_path: str | None = None) -> None:
        env_path = str(os.getenv("X_DEPLOYMENT_TARGETS_FILE", "") or "").strip()
        resolved = str(config_path or env_path).strip()
        if not resolved:
            resolved = str((_repo_root() / "config" / "deployment_targets.yaml").resolve())
        self.config_path = resolved

    def load(self) -> Dict[str, Dict[str, str]]:
        defaults = _default_targets()
        path = Path(self.config_path)
        if not path.exists():
            return defaults
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return defaults
        targets = payload.get("targets") if isinstance(payload, dict) else None
        if not isinstance(targets, dict):
            return defaults

        merged: Dict[str, Dict[str, str]] = {}
        for name, fallback in defaults.items():
            raw = targets.get(name)
            if not isinstance(raw, dict):
                merged[name] = dict(fallback)
                continue
            service = str(raw.get("service") or fallback["service"]).strip()
            image = str(raw.get("image") or fallback["image"]).strip()
            merged[name] = {
                "service": service or fallback["service"],
                "image": image or fallback["image"],
            }
        return merged

    def get(self, target_service: str) -> Dict[str, str] | None:
        safe_target = str(target_service or "").strip().lower()
        if not safe_target:
            return None
        targets = self.load()
        resolved = targets.get(safe_target)
        return dict(resolved) if isinstance(resolved, dict) else None


deployment_targets = DeploymentTargets()
