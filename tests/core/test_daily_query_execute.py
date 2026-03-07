import importlib.util
from pathlib import Path

import pytest


def _load_daily_query_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "skills"
        / "learned"
        / "daily_query"
        / "scripts"
        / "execute.py"
    )
    spec = importlib.util.spec_from_file_location("daily_query_execute_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _TimeoutClient:
    def __init__(self, *args, **kwargs):
        _ = (args, kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        _ = (exc_type, exc, tb)
        return False

    async def get(self, *args, **kwargs):
        _ = (args, kwargs)
        raise RuntimeError("wttr timeout")


@pytest.mark.asyncio
async def test_fetch_weather_falls_back_to_open_meteo(monkeypatch):
    module = _load_daily_query_module()

    async def fake_fallback(location: str):
        return {"text": f"FALLBACK:{location}", "ui": {}}

    monkeypatch.setattr(module.httpx, "AsyncClient", _TimeoutClient)
    monkeypatch.setattr(module, "_fetch_weather_open_meteo", fake_fallback)

    result = await module._fetch_weather("无锡")

    assert result["text"] == "FALLBACK:无锡"
