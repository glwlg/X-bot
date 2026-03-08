import base64
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

generate_image_module = pytest.importorskip(
    "skills.learned.generate_image.scripts.execute"
)
from core.skill_cli import prepare_default_env, _infer_skill_name, _resolve_output_dir


@pytest.mark.asyncio
async def test_learned_generate_image_uses_context_text_as_prompt(monkeypatch):
    async def _fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    class _FakeImages:
        def generate(self, **kwargs):
            assert kwargs["prompt"] == "画一只赛博朋克小狗"
            assert kwargs["model"] == "gpt-image-1"
            payload = base64.b64encode(b"fake-png-bytes").decode("utf-8")
            return SimpleNamespace(data=[SimpleNamespace(b64_json=payload)])

    fake_client = SimpleNamespace(images=_FakeImages())
    monkeypatch.setattr(generate_image_module.asyncio, "to_thread", _fake_to_thread)
    monkeypatch.setattr(
        generate_image_module,
        "get_image_generation_model",
        lambda: "openai/gpt-image-1",
    )
    monkeypatch.setattr(
        generate_image_module,
        "get_client_for_model",
        lambda _model_key, is_async=False: fake_client,
    )

    ctx = SimpleNamespace(message=SimpleNamespace(text="画一只赛博朋克小狗"))
    result = await generate_image_module.execute(ctx, {}, runtime=None)

    assert result.get("task_outcome") == "done"
    assert result.get("terminal") is True
    files = result.get("files") or {}
    assert files


@pytest.mark.asyncio
async def test_learned_generate_image_returns_recoverable_failure_when_prompt_missing():
    ctx = SimpleNamespace(message=SimpleNamespace(text=""))

    result = await generate_image_module.execute(ctx, {}, runtime=None)

    assert result.get("success") is False
    assert result.get("failure_mode") == "recoverable"


def test_prepare_default_env_sets_absolute_models_config_path(monkeypatch):
    monkeypatch.delenv("MODELS_CONFIG_PATH", raising=False)

    repo_root = Path(__file__).resolve().parents[2]
    prepare_default_env(repo_root)

    assert Path(str(os.environ["MODELS_CONFIG_PATH"])) == (
        repo_root / "config" / "models.json"
    ).resolve()


def test_prepare_default_env_normalizes_relative_models_config_path(monkeypatch):
    monkeypatch.setenv("MODELS_CONFIG_PATH", "config/models.json")

    repo_root = Path(__file__).resolve().parents[2]
    prepare_default_env(repo_root)

    assert Path(str(os.environ["MODELS_CONFIG_PATH"])) == (
        repo_root / "config" / "models.json"
    ).resolve()


def test_skill_cli_default_output_dir_uses_data_user_skill_path(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    args = SimpleNamespace(output_dir="", user_id="u-42")

    output_dir = _resolve_output_dir(args, execute_fn=generate_image_module.execute)

    assert Path(output_dir) == (
        tmp_path / "data" / "users" / "u-42" / "skills" / "generate_image" / "outputs"
    ).resolve()
    assert _infer_skill_name(generate_image_module.execute) == "generate_image"


@pytest.mark.asyncio
async def test_learned_generate_image_falls_back_to_chat_completions_when_images_api_404(
    monkeypatch,
):
    async def _fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    class _FakeImages:
        def generate(self, **kwargs):
            raise RuntimeError("404 page not found")

    class _FakeChatCompletions:
        def create(self, **kwargs):
            payload = base64.b64encode(b"chat-image-bytes").decode("utf-8")
            image = SimpleNamespace(
                image_url=SimpleNamespace(url=f"data:image/png;base64,{payload}")
            )
            message = SimpleNamespace(images=[image], content=None)
            choice = SimpleNamespace(message=message)
            return SimpleNamespace(choices=[choice])

    fake_client = SimpleNamespace(
        images=_FakeImages(),
        chat=SimpleNamespace(completions=_FakeChatCompletions()),
    )
    monkeypatch.setattr(generate_image_module.asyncio, "to_thread", _fake_to_thread)
    monkeypatch.setattr(
        generate_image_module,
        "get_image_generation_model",
        lambda: "proxy/gemini-3.1-flash-image-preview",
    )
    monkeypatch.setattr(
        generate_image_module,
        "get_client_for_model",
        lambda _model_key, is_async=False: fake_client,
    )

    ctx = SimpleNamespace(message=SimpleNamespace(text="画一只猫"))
    result = await generate_image_module.execute(ctx, {}, runtime=None)

    assert result.get("task_outcome") == "done"
    assert result.get("terminal") is True
    assert result.get("files")


@pytest.mark.asyncio
async def test_learned_generate_image_returns_fatal_when_both_images_and_chat_fail(
    monkeypatch,
):
    async def _fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    class _FakeImages:
        def generate(self, **kwargs):
            raise RuntimeError("404 page not found")

    class _FakeChatCompletions:
        def create(self, **kwargs):
            raise RuntimeError("chat image output not supported")

    fake_client = SimpleNamespace(
        images=_FakeImages(),
        chat=SimpleNamespace(completions=_FakeChatCompletions()),
    )
    monkeypatch.setattr(generate_image_module.asyncio, "to_thread", _fake_to_thread)
    monkeypatch.setattr(
        generate_image_module,
        "get_image_generation_model",
        lambda: "proxy/gemini-3.1-flash-image-preview",
    )
    monkeypatch.setattr(
        generate_image_module,
        "get_client_for_model",
        lambda _model_key, is_async=False: fake_client,
    )

    ctx = SimpleNamespace(message=SimpleNamespace(text="画一只猫"))
    result = await generate_image_module.execute(ctx, {}, runtime=None)

    assert result.get("success") is False
    assert result.get("failure_mode") == "fatal"
    assert "不支持生图" in str(result.get("text") or "")
