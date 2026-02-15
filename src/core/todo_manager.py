import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from core.config import DATA_DIR


def _safe_token(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(value or "").strip())
    return cleaned.strip("._") or "unknown"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _checkbox(status: str) -> str:
    if status == "done":
        return "x"
    return " "


@dataclass
class TaskTodoSession:
    user_id: str
    task_id: str
    goal: str
    task_dir: Path
    todo_path: Path
    heartbeat_path: Path
    created_at: str = field(default_factory=_now_iso)
    last_heartbeat_at: str = field(default_factory=_now_iso)
    steps: Dict[str, Dict[str, str]] = field(default_factory=dict)
    events: List[str] = field(default_factory=list)

    @classmethod
    def create(cls, user_id: str, task_id: str, goal: str) -> "TaskTodoSession":
        safe_user = _safe_token(str(user_id))
        safe_task = _safe_token(task_id)
        task_dir = (Path(DATA_DIR) / "runtime_tasks" / safe_user / safe_task).resolve()
        task_dir.mkdir(parents=True, exist_ok=True)

        session = cls(
            user_id=str(user_id),
            task_id=task_id,
            goal=(goal or "").strip() or "(empty user input)",
            task_dir=task_dir,
            todo_path=task_dir / "TODO.md",
            heartbeat_path=task_dir / "heartbeat.json",
        )
        session.steps = {
            "plan": {
                "title": "Clarify goal and plan",
                "status": "in_progress",
                "detail": "Task received.",
                "updated_at": session.created_at,
            },
            "act": {
                "title": "Execute tools/extensions",
                "status": "pending",
                "detail": "",
                "updated_at": session.created_at,
            },
            "verify": {
                "title": "Verify outcome",
                "status": "pending",
                "detail": "",
                "updated_at": session.created_at,
            },
            "deliver": {
                "title": "Deliver final response",
                "status": "pending",
                "detail": "",
                "updated_at": session.created_at,
            },
        }
        session.add_event("Task session initialized.")
        return session

    def mark_step(self, key: str, status: str, detail: str = "") -> None:
        if key not in self.steps:
            return
        item = self.steps[key]
        item["status"] = status
        item["detail"] = detail.strip()
        item["updated_at"] = _now_iso()
        self._persist()

    def add_event(self, message: str) -> None:
        note = (message or "").strip()
        if not note:
            return
        self.events.append(f"{_now_iso()} | {note}")
        if len(self.events) > 30:
            self.events = self.events[-30:]
        self._persist()

    def heartbeat(self, note: str = "") -> None:
        self.last_heartbeat_at = _now_iso()
        if note:
            self.add_event(f"heartbeat: {note}")
        else:
            self._persist()

    def mark_failed(self, reason: str) -> None:
        self.mark_step("deliver", "blocked", reason)
        self.add_event(f"blocked: {reason}")

    def mark_completed(self, summary: str = "") -> None:
        self.mark_step("plan", "done", self.steps["plan"].get("detail", "") or "done")
        self.mark_step("act", "done", self.steps["act"].get("detail", "") or "done")
        self.mark_step(
            "verify", "done", self.steps["verify"].get("detail", "") or "done"
        )
        self.mark_step("deliver", "done", summary or "Final response returned.")
        self.add_event("task completed")

    def _persist(self) -> None:
        self.todo_path.write_text(self._render_todo_markdown(), encoding="utf-8")
        self.heartbeat_path.write_text(
            json.dumps(
                {
                    "task_id": self.task_id,
                    "user_id": self.user_id,
                    "updated_at": self.last_heartbeat_at,
                    "events": self.events[-5:],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _render_todo_markdown(self) -> str:
        lines = [
            "# TODO",
            "",
            f"- Task ID: `{self.task_id}`",
            f"- User: `{self.user_id}`",
            f"- Created: `{self.created_at}`",
            f"- Last heartbeat: `{self.last_heartbeat_at}`",
            "",
            "## Goal",
            f"> {self.goal}",
            "",
            "## Steps",
        ]

        for key in ("plan", "act", "verify", "deliver"):
            item = self.steps.get(key) or {}
            title = item.get("title", key)
            status = item.get("status", "pending")
            detail = item.get("detail", "")
            updated_at = item.get("updated_at", self.created_at)
            lines.append(
                f"- [{_checkbox(status)}] `{key}` {title} ({status}, {updated_at})"
            )
            if detail:
                lines.append(f"  - {detail}")

        lines.append("")
        lines.append("## Recent Events")
        if self.events:
            for item in self.events[-15:]:
                lines.append(f"- {item}")
        else:
            lines.append("- (none)")
        lines.append("")

        return "\n".join(lines)
