from __future__ import annotations

from typing import Any, Dict, Protocol

from shared.contracts.dispatch import TaskEnvelope, TaskResult


class WorkerProgram(Protocol):
    async def run(self, task: TaskEnvelope, context: Dict[str, Any]) -> TaskResult: ...
