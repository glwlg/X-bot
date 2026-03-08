from __future__ import annotations

from typing import Any


def normalize_target_service(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in {"manager", "worker", "api"}:
        return token
    return "manager"


def normalize_rollout_mode(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in {"none", "local"}:
        return token
    return "none"
