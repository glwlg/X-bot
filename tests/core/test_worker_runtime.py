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
