import base64
from types import SimpleNamespace

import pytest

import skills.builtin.generate_image.scripts.execute as generate_image_module


@pytest.mark.asyncio
async def test_generate_image_uses_context_text_as_prompt(monkeypatch):
    class _FakeImages:
        def generate(self, **kwargs):
            assert kwargs["prompt"] == "画一只赛博朋克小狗"
            payload = base64.b64encode(b"fake-png-bytes").decode("utf-8")
            return SimpleNamespace(data=[SimpleNamespace(b64_json=payload)])

    fake_client = SimpleNamespace(images=_FakeImages())
    monkeypatch.setattr(generate_image_module, "openai_client", fake_client)

    ctx = SimpleNamespace(message=SimpleNamespace(text="画一只赛博朋克小狗"))
    result = await generate_image_module.execute(ctx, {}, runtime=None)

    assert result.get("task_outcome") == "done"
    assert result.get("terminal") is True
    files = result.get("files") or {}
    assert files


@pytest.mark.asyncio
async def test_generate_image_returns_recoverable_failure_when_prompt_missing(
    monkeypatch,
):
    monkeypatch.setattr(generate_image_module, "openai_client", None)
    ctx = SimpleNamespace(message=SimpleNamespace(text=""))

    result = await generate_image_module.execute(ctx, {}, runtime=None)

    assert result.get("success") is False
    assert result.get("failure_mode") == "recoverable"
