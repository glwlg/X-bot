import asyncio
import json
from pathlib import Path

import pytest

from shared.queue.dispatch_queue import DispatchQueue
from worker.kernel.daemon import WorkerKernelDaemon
from worker.kernel.program_loader import ProgramLoader


def _write_program(
    root: Path,
    *,
    program_id: str,
    version: str,
    code: str,
) -> None:
    version_dir = (root / program_id / version).resolve()
    version_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "program_id": program_id,
        "version": version,
        "entrypoint": "program.py",
        "checksum": "dev",
        "created_by": "test",
    }
    (version_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (version_dir / "program.py").write_text(code, encoding="utf-8")


@pytest.mark.asyncio
async def test_program_loader_loads_program(tmp_path, monkeypatch):
    programs_root = (tmp_path / "programs").resolve()
    _write_program(
        programs_root,
        program_id="p1",
        version="v1",
        code=(
            "from shared.contracts.dispatch import TaskResult\n"
            "class Program:\n"
            "    async def run(self, task, context):\n"
            "        return TaskResult(task_id=task.task_id, worker_id=context['worker_id'], ok=True, summary='ok', payload={'text':'ok'})\n"
            "def build_program():\n"
            "    return Program()\n"
        ),
    )
    monkeypatch.setenv("WORKER_PROGRAMS_ROOT", str(programs_root))

    loader = ProgramLoader()
    program = loader.load_program(program_id="p1", version="v1")
    assert program is not None


@pytest.mark.asyncio
async def test_worker_kernel_executes_task_and_writes_result(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WORKER_KERNEL_ID", "worker-main")
    monkeypatch.setenv("WORKER_DEFAULT_PROGRAM_ID", "default-worker")
    monkeypatch.setenv("WORKER_DEFAULT_PROGRAM_VERSION", "v1")

    programs_root = (tmp_path / "system" / "worker_programs").resolve()
    _write_program(
        programs_root,
        program_id="default-worker",
        version="v1",
        code=(
            "from shared.contracts.dispatch import TaskResult\n"
            "class Program:\n"
            "    async def run(self, task, context):\n"
            "        return TaskResult(task_id=task.task_id, worker_id=context['worker_id'], ok=True, summary='done', payload={'text':'done'})\n"
            "def build_program():\n"
            "    return Program()\n"
        ),
    )

    queue = DispatchQueue()
    submitted = await queue.submit_task(
        worker_id="worker-main",
        instruction="run test",
        source="manager_dispatch",
        metadata={
            "user_id": "u1",
            "program_id": "default-worker",
            "program_version": "v1",
        },
    )

    daemon = WorkerKernelDaemon(queue=queue, loader=ProgramLoader())
    await daemon._tick()
    if daemon._running:
        await asyncio.gather(*daemon._running)

    rows = await queue.list_tasks(worker_id="worker-main", limit=10)
    assert rows
    assert rows[0].task_id == submitted.task_id
    assert rows[0].status == "done"

    result = await queue.latest_result(submitted.task_id)
    assert result is not None
    assert result.ok is True
    assert "done" in str(result.payload.get("text") or "")
    assert result.payload.get("_result_writer") == "worker_kernel"
    assert result.payload.get("_execution_path") == "worker.kernel.daemon"
    assert result.payload.get("_program_id") == "default-worker"
    assert result.payload.get("_program_version") == "v1"


@pytest.mark.asyncio
async def test_worker_kernel_marks_failed_on_program_error(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WORKER_KERNEL_ID", "worker-main")
    monkeypatch.setenv("WORKER_DEFAULT_PROGRAM_ID", "default-worker")
    monkeypatch.setenv("WORKER_DEFAULT_PROGRAM_VERSION", "v1")

    programs_root = (tmp_path / "system" / "worker_programs").resolve()
    _write_program(
        programs_root,
        program_id="default-worker",
        version="v1",
        code=(
            "class Program:\n"
            "    async def run(self, task, context):\n"
            "        raise RuntimeError('boom')\n"
            "def build_program():\n"
            "    return Program()\n"
        ),
    )

    queue = DispatchQueue()
    submitted = await queue.submit_task(
        worker_id="worker-main",
        instruction="run test",
        source="manager_dispatch",
        metadata={
            "user_id": "u1",
            "program_id": "default-worker",
            "program_version": "v1",
        },
    )

    daemon = WorkerKernelDaemon(queue=queue, loader=ProgramLoader())
    await daemon._tick()
    if daemon._running:
        await asyncio.gather(*daemon._running)

    rows = await queue.list_tasks(worker_id="worker-main", limit=10)
    assert rows
    assert rows[0].task_id == submitted.task_id
    assert rows[0].status == "failed"

    result = await queue.latest_result(submitted.task_id)
    assert result is not None
    assert result.ok is False
    assert "boom" in str(result.error or result.summary)
    assert result.payload.get("_result_writer") == "worker_kernel"
    assert result.payload.get("_program_id") == "default-worker"


@pytest.mark.asyncio
async def test_worker_kernel_stops_running_task_after_queue_cancel(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WORKER_KERNEL_ID", "worker-main")
    monkeypatch.setenv("WORKER_DEFAULT_PROGRAM_ID", "default-worker")
    monkeypatch.setenv("WORKER_DEFAULT_PROGRAM_VERSION", "v1")
    monkeypatch.setenv("WORKER_TASK_CANCEL_POLL_SEC", "0.05")

    programs_root = (tmp_path / "system" / "worker_programs").resolve()
    _write_program(
        programs_root,
        program_id="default-worker",
        version="v1",
        code=(
            "import asyncio\n"
            "from shared.contracts.dispatch import TaskResult\n"
            "class Program:\n"
            "    async def run(self, task, context):\n"
            "        await asyncio.sleep(30)\n"
            "        return TaskResult(task_id=task.task_id, worker_id=context['worker_id'], ok=True, summary='late', payload={'text':'late'})\n"
            "def build_program():\n"
            "    return Program()\n"
        ),
    )

    queue = DispatchQueue()
    submitted = await queue.submit_task(
        worker_id="worker-main",
        instruction="long run",
        source="manager_dispatch",
        metadata={
            "user_id": "u-stop",
            "program_id": "default-worker",
            "program_version": "v1",
        },
    )

    daemon = WorkerKernelDaemon(queue=queue, loader=ProgramLoader())
    await daemon._tick()
    assert daemon._running

    cancelled = await queue.cancel_for_user(
        user_id="u-stop",
        reason="cancelled_by_stop_command",
        include_running=True,
    )
    assert cancelled["running_signaled"] == 1

    await asyncio.wait_for(asyncio.gather(*daemon._running), timeout=3)

    task_row = await queue.get_task(submitted.task_id)
    assert task_row is not None
    assert task_row.status == "cancelled"
    assert task_row.error == "cancelled_by_stop_command"

    result = await queue.latest_result(submitted.task_id)
    assert result is not None
    assert result.ok is False
    assert result.payload.get("cancelled") is True
    assert result.payload.get("cancel_reason") == "cancelled_by_stop_command"


def test_program_loader_refreshes_legacy_bootstrap_artifact(tmp_path, monkeypatch):
    programs_root = (tmp_path / "programs").resolve()
    legacy_dir = (programs_root / "default-worker" / "v1").resolve()
    legacy_dir.mkdir(parents=True, exist_ok=True)

    legacy_manifest = {
        "program_id": "default-worker",
        "version": "v1",
        "entrypoint": "program.py",
        "checksum": "bootstrap",
        "created_by": "bootstrap",
        "metadata": {"source": "auto_bootstrap"},
    }
    (legacy_dir / "manifest.json").write_text(
        json.dumps(legacy_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (legacy_dir / "program.py").write_text(
        "print('legacy bootstrap')\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("WORKER_PROGRAMS_ROOT", str(programs_root))
    loader = ProgramLoader()
    loader.ensure_program_artifact(program_id="default-worker", version="v1")

    refreshed_manifest = json.loads(
        (legacy_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert refreshed_manifest.get("checksum") == "bootstrap-core-agent-v2"
    assert refreshed_manifest.get("created_by") == "bootstrap-core-agent-v2"
    assert (
        str((refreshed_manifest.get("metadata") or {}).get("source") or "")
        == "bootstrap_core_agent_v2"
    )

    entrypoint = (legacy_dir / "program.py").read_text(encoding="utf-8")
    assert "from worker.programs.core_agent_program import build_program" in entrypoint
