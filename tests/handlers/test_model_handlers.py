from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.model_config as model_config_module
import handlers.model_handlers as model_handlers
from handlers import model_command as exported_model_command
from handlers.model_handlers import handle_model_callback, model_command


class _FakeUser:
    def __init__(self, user_id: str):
        self.id = user_id


class _FakeMessage:
    def __init__(self, text: str, user_id: str):
        self.id = "m-model"
        self.text = text
        self.user = _FakeUser(user_id)


class _FakeContext:
    def __init__(
        self,
        text: str,
        user_id: str = "u-model",
        *,
        callback_data: str = "",
    ):
        self.message = _FakeMessage(text, user_id)
        self.replies: list[str] = []
        self.reply_calls: list[dict] = []
        self.edit_calls: list[dict] = []
        self.user_data: dict = {}
        self.callback_data = callback_data

    async def reply(self, text: str, **kwargs):
        self.replies.append(str(text))
        self.reply_calls.append({"text": str(text), "kwargs": dict(kwargs)})
        return SimpleNamespace(id="reply")

    async def edit_message(self, message_id: str, text: str, **kwargs):
        self.edit_calls.append(
            {
                "message_id": str(message_id),
                "text": str(text),
                "kwargs": dict(kwargs),
            }
        )
        return SimpleNamespace(id=message_id)

    async def answer_callback(self, text: str | None = None, show_alert: bool = False):
        _ = text
        _ = show_alert
        return True


def _reset_model_config_state(monkeypatch) -> None:
    monkeypatch.setattr(model_config_module, "_models_config", None)
    monkeypatch.setattr(model_config_module, "_model_manager", None)
    monkeypatch.setattr(model_config_module, "_primary_model", "")


def _write_models_config(tmp_path: Path) -> Path:
    config_path = (tmp_path / "models.json").resolve()
    config_path.write_text(
        json.dumps(
            {
                "model": {
                    "primary": "demo/text",
                    "routing": "demo/router",
                    "image": "demo/vision",
                    "image_generation": "demo/image-gen",
                    "voice": "demo/voice",
                },
                "models": {
                    "primary": {
                        "demo/text": {},
                        "demo/fallback": {},
                    },
                    "routing": {
                        "demo/router": {},
                    },
                    "image": {
                        "demo/vision": {},
                    },
                },
                "providers": {
                    "demo": {
                        "baseUrl": "https://example.invalid/v1",
                        "apiKey": "test-key",
                        "models": [
                            {"id": "text", "name": "text", "input": ["text"]},
                            {
                                "id": "fallback",
                                "name": "fallback",
                                "input": ["text"],
                            },
                            {"id": "router", "name": "router", "input": ["text"]},
                            {"id": "vision", "name": "vision", "input": ["image"]},
                            {
                                "id": "image-gen",
                                "name": "image-gen",
                                "input": ["image"],
                            },
                            {"id": "voice", "name": "voice", "input": ["voice"]},
                        ],
                    }
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return config_path


@pytest.mark.asyncio
async def test_model_command_shows_current_config(monkeypatch, tmp_path):
    config_path = _write_models_config(tmp_path)
    monkeypatch.setenv("MODELS_CONFIG_PATH", str(config_path))
    _reset_model_config_state(monkeypatch)

    async def _allow(_ctx):
        return True

    monkeypatch.setattr(model_handlers, "check_permission_unified", _allow)

    ctx = _FakeContext("/model")
    await model_command(ctx)

    assert ctx.replies
    reply = ctx.replies[-1]
    assert "当前模型配置" in reply
    assert "demo/text" in reply
    assert "demo/router" in reply
    assert "demo/image-gen" in reply
    ui = ctx.reply_calls[-1]["kwargs"]["ui"]
    assert ui["actions"][0][0]["callback_data"] == "model_role:primary:0"


@pytest.mark.asyncio
async def test_model_command_lists_defined_models(monkeypatch, tmp_path):
    config_path = _write_models_config(tmp_path)
    monkeypatch.setenv("MODELS_CONFIG_PATH", str(config_path))
    _reset_model_config_state(monkeypatch)

    async def _allow(_ctx):
        return True

    monkeypatch.setattr(model_handlers, "check_permission_unified", _allow)

    ctx = _FakeContext("/model list")
    await model_command(ctx)

    assert ctx.replies
    reply = ctx.replies[-1]
    assert "已定义模型列表" in reply
    assert "demo/text" in reply
    assert "demo/fallback" in reply
    assert "selected=主对话" in reply
    ui = ctx.reply_calls[-1]["kwargs"]["ui"]
    assert ui["actions"][0][0]["callback_data"] == "model_role:primary:0"


@pytest.mark.asyncio
async def test_model_command_updates_primary_model_and_reloads(monkeypatch, tmp_path):
    config_path = _write_models_config(tmp_path)
    monkeypatch.setenv("MODELS_CONFIG_PATH", str(config_path))
    _reset_model_config_state(monkeypatch)

    async def _allow(_ctx):
        return True

    monkeypatch.setattr(model_handlers, "check_permission_unified", _allow)

    ctx = _FakeContext("/model use primary demo/fallback")
    await model_command(ctx)

    assert ctx.replies
    reply = ctx.replies[-1]
    assert "模型配置已更新" in reply
    assert "demo/fallback" in reply

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["model"]["primary"] == "demo/fallback"
    assert model_config_module.get_current_model() == "demo/fallback"


@pytest.mark.asyncio
async def test_model_command_updates_primary_model_without_explicit_role(
    monkeypatch, tmp_path
):
    config_path = _write_models_config(tmp_path)
    monkeypatch.setenv("MODELS_CONFIG_PATH", str(config_path))
    _reset_model_config_state(monkeypatch)

    async def _allow(_ctx):
        return True

    monkeypatch.setattr(model_handlers, "check_permission_unified", _allow)

    ctx = _FakeContext("/model use demo/fallback")
    await model_command(ctx)

    assert ctx.replies
    reply = ctx.replies[-1]
    assert "模型配置已更新" in reply
    assert "primary" in reply

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["model"]["primary"] == "demo/fallback"
    assert model_config_module.get_current_model() == "demo/fallback"


@pytest.mark.asyncio
async def test_model_callback_supports_role_menu_and_click_selection(
    monkeypatch, tmp_path
):
    config_path = _write_models_config(tmp_path)
    monkeypatch.setenv("MODELS_CONFIG_PATH", str(config_path))
    _reset_model_config_state(monkeypatch)

    async def _allow(_ctx):
        return True

    monkeypatch.setattr(model_handlers, "check_permission_unified", _allow)

    ctx = _FakeContext("/model", callback_data="model_role:primary:0")
    await handle_model_callback(ctx)

    assert ctx.edit_calls
    first_edit = ctx.edit_calls[-1]
    assert "选择 主对话(primary) 模型" in first_edit["text"]
    assert first_edit["kwargs"]["ui"]["actions"][0][0]["callback_data"] == (
        "model_set:primary:0"
    )

    ctx.callback_data = "model_set:primary:1"
    await handle_model_callback(ctx)

    assert len(ctx.edit_calls) >= 2
    second_edit = ctx.edit_calls[-1]
    assert "已通过菜单切换模型" in second_edit["text"]

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["model"]["primary"] == "demo/fallback"
    assert model_config_module.get_current_model() == "demo/fallback"


def test_model_command_is_exported_from_handlers_package():
    assert exported_model_command is model_command


def test_main_registers_model_command():
    main_py = Path(__file__).resolve().parents[2] / "src" / "main.py"
    text = main_py.read_text(encoding="utf-8")

    assert 'on_command("model", model_command' in text
    assert 'on_callback_query("^model_", handle_model_callback)' in text
