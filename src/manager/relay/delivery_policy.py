from __future__ import annotations

from shared.contracts.dispatch import TaskEnvelope


def is_heartbeat_delivery(task: TaskEnvelope) -> bool:
    if str(task.source or "").strip().lower() == "heartbeat":
        return True
    metadata = dict(task.metadata or {})
    candidates = (
        metadata.get("session_id"),
        metadata.get("session_task_id"),
        metadata.get("task_goal"),
        metadata.get("original_user_request"),
    )
    for value in candidates:
        text = str(value or "").strip().lower()
        if text.startswith("hb-") or text == "heartbeat":
            return True
    return False


def delivery_priority_for_task(task: TaskEnvelope) -> str:
    source = str(task.source or "").strip().lower()
    if is_heartbeat_delivery(task) or source in {"heartbeat", "rss", "scheduler", "cron"}:
        return "background"
    return "interactive"


def delivery_body_mode_for_task(task: TaskEnvelope) -> str:
    if delivery_priority_for_task(task) == "background":
        return "raw_text"
    return "auto"
