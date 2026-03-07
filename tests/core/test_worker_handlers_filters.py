from handlers.worker_handlers import _parse_subcommand, _parse_tasks_filters
import handlers.worker_handlers as worker_handlers_module


class _FakeCtx:
    def __init__(self, text: str):
        self.message = type(
            "Msg",
            (),
            {
                "text": text,
                "user": type("User", (), {"id": "u-1"})(),
            },
        )()
        self.replies: list[str] = []

    async def reply(self, text: str):
        self.replies.append(str(text))


def test_worker_tasks_filter_defaults_to_user_chat_without_heartbeat():
    include, exclude = _parse_tasks_filters("")
    assert include == ["user_chat"]
    assert exclude == ["heartbeat"]


def test_worker_tasks_filter_all():
    include, exclude = _parse_tasks_filters("all")
    assert include is None
    assert exclude is None


def test_worker_tasks_filter_custom_args():
    include, exclude = _parse_tasks_filters(
        "source=user_cmd,user_chat exclude=heartbeat,system"
    )
    assert include == ["user_cmd", "user_chat"]
    assert exclude == ["heartbeat", "system"]


def test_worker_subcommand_defaults_to_list_and_supports_help():
    assert _parse_subcommand("/worker") == ("list", "")
    assert _parse_subcommand("/worker help") == ("help", "")


async def test_worker_tasks_prefers_worker_task_store_rows(monkeypatch):
    async def fake_permission(_ctx):
        return True

    async def fake_resolve_active_worker(_user_id: str) -> str:
        return "worker-main"

    async def fake_list_recent(**_kwargs):
        return [
            {
                "task_id": "wt-1",
                "status": "done",
                "source": "user_chat",
                "retry_count": 2,
                "error": "",
            }
        ]

    async def fake_dispatch_rows(**_kwargs):
        return []

    monkeypatch.setattr(
        worker_handlers_module,
        "check_permission_unified",
        fake_permission,
    )
    monkeypatch.setattr(
        worker_handlers_module,
        "_resolve_active_worker",
        fake_resolve_active_worker,
    )
    monkeypatch.setattr(
        worker_handlers_module.worker_task_store,
        "list_recent",
        fake_list_recent,
    )
    monkeypatch.setattr(
        worker_handlers_module.dispatch_queue,
        "list_tasks",
        fake_dispatch_rows,
    )

    ctx = _FakeCtx("/worker tasks")
    await worker_handlers_module.worker_command(ctx)

    assert ctx.replies
    assert "wt-1" in ctx.replies[0]


async def test_worker_tasks_uses_dispatch_status_as_latest_snapshot(monkeypatch):
    from shared.contracts.dispatch import TaskEnvelope

    async def fake_permission(_ctx):
        return True

    async def fake_resolve_active_worker(_user_id: str) -> str:
        return "worker-main"

    async def fake_list_recent(**_kwargs):
        return [
            {
                "task_id": "tsk-1",
                "status": "queued",
                "source": "user_chat",
                "retry_count": 0,
                "error": "",
            }
        ]

    async def fake_list_tasks(**_kwargs):
        return [
            TaskEnvelope(
                task_id="tsk-1",
                worker_id="worker-main",
                instruction="run",
                source="user_chat",
                status="done",
                retry_count=1,
                error="",
            )
        ]

    monkeypatch.setattr(
        worker_handlers_module,
        "check_permission_unified",
        fake_permission,
    )
    monkeypatch.setattr(
        worker_handlers_module,
        "_resolve_active_worker",
        fake_resolve_active_worker,
    )
    monkeypatch.setattr(
        worker_handlers_module.worker_task_store,
        "list_recent",
        fake_list_recent,
    )
    monkeypatch.setattr(
        worker_handlers_module.dispatch_queue,
        "list_tasks",
        fake_list_tasks,
    )

    ctx = _FakeCtx("/worker tasks")
    await worker_handlers_module.worker_command(ctx)

    assert ctx.replies
    assert "tsk-1" in ctx.replies[0]
    assert "done" in ctx.replies[0]


async def test_worker_tasks_falls_back_to_dispatch_queue(monkeypatch):
    from shared.contracts.dispatch import TaskEnvelope

    async def fake_permission(_ctx):
        return True

    async def fake_resolve_active_worker(_user_id: str) -> str:
        return "worker-main"

    async def fake_list_recent(**_kwargs):
        return []

    async def fake_list_tasks(**_kwargs):
        return [
            TaskEnvelope(
                task_id="tsk-1",
                worker_id="worker-main",
                instruction="run",
                source="user_chat",
                status="done",
                retry_count=1,
                error="",
            )
        ]

    monkeypatch.setattr(
        worker_handlers_module,
        "check_permission_unified",
        fake_permission,
    )
    monkeypatch.setattr(
        worker_handlers_module,
        "_resolve_active_worker",
        fake_resolve_active_worker,
    )
    monkeypatch.setattr(
        worker_handlers_module.worker_task_store,
        "list_recent",
        fake_list_recent,
    )
    monkeypatch.setattr(
        worker_handlers_module.dispatch_queue,
        "list_tasks",
        fake_list_tasks,
    )

    ctx = _FakeCtx("/worker tasks")
    await worker_handlers_module.worker_command(ctx)

    assert ctx.replies
    assert "tsk-1" in ctx.replies[0]
