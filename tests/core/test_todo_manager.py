import json
import os

os.environ.setdefault("GEMINI_API_KEY", "test-key")

from core.todo_manager import TaskTodoSession
import core.todo_manager as todo_manager_module


def test_todo_manager_creates_todo_and_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setattr(todo_manager_module, "DATA_DIR", str(tmp_path))

    session = TaskTodoSession.create(user_id="123", task_id="task-a", goal="部署 n8n")
    assert session.todo_path.exists()
    assert session.heartbeat_path.exists()

    session.mark_step("act", "in_progress", "running docker compose")
    session.heartbeat("step:update")

    content = session.todo_path.read_text(encoding="utf-8")
    heartbeat = json.loads(session.heartbeat_path.read_text(encoding="utf-8"))

    assert "# TODO" in content
    assert "部署 n8n" in content
    assert "running docker compose" in content
    assert heartbeat["task_id"] == "task-a"
    assert any("heartbeat: step:update" in item for item in heartbeat["events"])
