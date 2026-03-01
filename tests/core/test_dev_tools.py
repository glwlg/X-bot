import importlib

import pytest


dev_tools_module = importlib.import_module("core.tools.dev_tools")


class _FakeManagerDevService:
    def __init__(self):
        self.calls = []

    async def software_delivery(self, **kwargs):
        self.calls.append(dict(kwargs))
        return {
            "ok": True,
            "task_id": "dev-1",
            "status": "done",
            "summary": "software delivery done",
            "terminal": True,
            "task_outcome": "done",
        }


@pytest.mark.asyncio
async def test_software_delivery_delegates_to_manager_service(monkeypatch):
    fake_service = _FakeManagerDevService()
    monkeypatch.setattr(dev_tools_module, "manager_dev_service", fake_service)

    tools = dev_tools_module.DevTools()
    result = await tools.software_delivery(
        action="run",
        requirement="fix issue",
        issue="org/repo#1",
        auto_publish=False,
    )

    assert result["ok"] is True
    assert result["status"] == "done"
    assert fake_service.calls
    assert fake_service.calls[0]["action"] == "run"
    assert fake_service.calls[0]["issue"] == "org/repo#1"
    assert fake_service.calls[0]["auto_publish"] is False
