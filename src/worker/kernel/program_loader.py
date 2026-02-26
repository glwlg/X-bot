from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from shared.contracts.programs import ProgramManifest
from worker.program_api import WorkerProgram


class ProgramLoader:
    def __init__(self) -> None:
        data_dir = os.getenv("DATA_DIR", "/app/data").strip() or "/app/data"
        default_root = os.path.join(data_dir, "system", "worker_programs")
        self.programs_root = (
            Path(
                os.getenv("WORKER_PROGRAMS_ROOT", default_root).strip() or default_root
            )
            .expanduser()
            .resolve()
        )

    def _version_dir(self, program_id: str, version: str) -> Path:
        safe_program = str(program_id or "").strip()
        safe_version = str(version or "").strip()
        return (self.programs_root / safe_program / safe_version).resolve()

    def ensure_program_artifact(self, *, program_id: str, version: str) -> Path:
        version_dir = self._version_dir(program_id, version)
        version_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = (version_dir / "manifest.json").resolve()
        entrypoint_path = (version_dir / "program.py").resolve()

        if not manifest_path.exists():
            manifest = ProgramManifest(
                program_id=str(program_id or "").strip(),
                version=str(version or "").strip(),
                entrypoint="program.py",
                checksum="bootstrap",
                created_by="bootstrap",
                metadata={"source": "auto_bootstrap"},
            )
            manifest_path.write_text(
                json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        if not entrypoint_path.exists():
            entrypoint_path.write_text(
                (
                    "from shared.contracts.dispatch import TaskResult\n"
                    "\n"
                    "class Program:\n"
                    "    async def run(self, task, context):\n"
                    "        text = str(task.instruction or '').strip()\n"
                    "        if not text:\n"
                    "            text = 'empty task instruction'\n"
                    "        return TaskResult(\n"
                    "            task_id=task.task_id,\n"
                    "            worker_id=str(context.get('worker_id') or task.worker_id),\n"
                    "            ok=True,\n"
                    "            summary=text[:200],\n"
                    "            payload={'text': text},\n"
                    "        )\n"
                    "\n"
                    "def build_program():\n"
                    "    return Program()\n"
                ),
                encoding="utf-8",
            )

        return version_dir

    def load_manifest(self, *, program_id: str, version: str) -> ProgramManifest:
        version_dir = self._version_dir(program_id, version)
        manifest_path = (version_dir / "manifest.json").resolve()
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"invalid manifest payload: {manifest_path}")
        manifest = ProgramManifest.from_dict(payload)
        if not manifest.program_id or not manifest.version:
            raise ValueError(f"invalid manifest fields: {manifest_path}")
        return manifest

    def _load_module(self, entrypoint_path: Path) -> ModuleType:
        module_name = f"worker_program_{entrypoint_path.stem}_{entrypoint_path.stat().st_mtime_ns}"
        spec = importlib.util.spec_from_file_location(module_name, entrypoint_path)
        if not spec or not spec.loader:
            raise RuntimeError(f"failed to load module spec: {entrypoint_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def load_program(self, *, program_id: str, version: str) -> WorkerProgram:
        manifest = self.load_manifest(program_id=program_id, version=version)
        version_dir = self._version_dir(program_id, version)
        entrypoint = str(manifest.entrypoint or "program.py").strip() or "program.py"
        entrypoint_path = (version_dir / entrypoint).resolve()
        if not entrypoint_path.exists() or not entrypoint_path.is_file():
            raise FileNotFoundError(f"program entrypoint not found: {entrypoint_path}")

        module = self._load_module(entrypoint_path)

        if hasattr(module, "build_program"):
            builder = getattr(module, "build_program")
            if not callable(builder):
                raise RuntimeError(f"build_program is not callable: {entrypoint_path}")
            program = builder()
            if program is None:
                raise RuntimeError(f"build_program returned None: {entrypoint_path}")
            return cast(WorkerProgram, program)

        if hasattr(module, "PROGRAM"):
            program = getattr(module, "PROGRAM")
            if program is None:
                raise RuntimeError(f"PROGRAM is None: {entrypoint_path}")
            return cast(WorkerProgram, program)

        raise RuntimeError(
            f"worker program must export build_program() or PROGRAM: {entrypoint_path}"
        )


program_loader = ProgramLoader()
