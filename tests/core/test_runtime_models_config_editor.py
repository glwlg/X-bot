import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import core.model_config as model_config_module
from api.auth.models import User, UserRole
from api.schemas.admin_config import (
    ModelsLatencyCheckRequest,
    RuntimeChannelsPatch,
    RuntimeDocGenerateRequest,
    RuntimeTelegramChannelPatch,
    RuntimeWebChannelPatch,
    RuntimeWeixinChannelPatch,
)
from api.services import admin_config_service
from core.audit_store import audit_store


def _redirect_audit_paths(tmp_path: Path) -> None:
    audit_root = (tmp_path / "audit").resolve()
    versions_root = (tmp_path / "versions").resolve()
    index_root = (audit_root / "index").resolve()
    logs_root = (audit_root / "logs").resolve()
    audit_root.mkdir(parents=True, exist_ok=True)
    versions_root.mkdir(parents=True, exist_ok=True)
    index_root.mkdir(parents=True, exist_ok=True)
    logs_root.mkdir(parents=True, exist_ok=True)
    audit_store.audit_root = audit_root
    audit_store.versions_root = versions_root
    audit_store.index_root = index_root
    audit_store.logs_root = logs_root
    audit_store.events_path = (audit_root / "events.jsonl").resolve()
    audit_store.version_retention_count = 3
    audit_store.log_retention_days = 30
    audit_store._legacy_migrated = False


def _reset_model_config_state(monkeypatch) -> None:
    monkeypatch.setattr(model_config_module, "_models_config", None)
    monkeypatch.setattr(model_config_module, "_model_manager", None)
    monkeypatch.setattr(model_config_module, "_primary_model", "")
    monkeypatch.setattr(model_config_module, "_loaded_config_path", None)
    monkeypatch.setattr(model_config_module, "_loaded_config_mtime_ns", None)


def _models_payload() -> dict:
    return {
        "mode": "merge",
        "model": {
            "primary": "demo/text",
            "routing": "demo/text",
            "vision": "demo/vision",
        },
        "models": {
            "primary": {
                "demo/text": {
                    "priority": 1,
                }
            },
            "routing": {
                "demo/text": {}
            },
            "vision": ["demo/vision"],
        },
        "selection": {
            "primary": {
                "strategy": "round_robin",
                "sticky": False,
            },
            "vision": {
                "strategy": "least_usage",
            },
        },
        "providers": {
            "demo": {
                "baseUrl": "https://example.invalid/v1",
                "apiKey": "test-key",
                "api": "openai-completions",
                "headers": {
                    "X-Trace": "demo",
                },
                "models": [
                    {
                        "id": "text",
                        "name": "Demo Text",
                        "reasoning": False,
                        "input": ["text"],
                        "output": ["text"],
                        "temperature": 0.2,
                        "cost": {
                            "input": 1,
                            "output": 2,
                            "cacheRead": 3,
                            "cacheWrite": 4,
                            "discount": 0.5,
                        },
                        "limits": {
                            "dailyTokens": 120000,
                            "dailyImages": 0,
                            "burst": 2,
                        },
                        "contextWindow": 123456,
                        "maxTokens": 4096,
                    },
                    {
                        "id": "vision",
                        "name": "Demo Vision",
                        "reasoning": True,
                        "input": ["text", "image"],
                        "output": ["text"],
                        "limits": {
                            "dailyTokens": 80000,
                        },
                        "labels": ["vision"],
                    },
                ],
            }
        },
        "customTopLevel": {
            "enabled": True,
        },
    }


def _build_admin_user() -> User:
    return User(
        id=7,
        email="admin@example.com",
        hashed_password="hashed",
        is_active=True,
        is_superuser=True,
        is_verified=True,
        username="admin",
        display_name="Admin",
        role=UserRole.ADMIN,
    )


def test_runtime_snapshot_uses_runtime_shape_and_models_snapshot_includes_editor_payload(
    tmp_path, monkeypatch
):
    config_path = (tmp_path / "models.json").resolve()
    payload = _models_payload()
    config_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("MODELS_CONFIG_PATH", str(config_path))
    _reset_model_config_state(monkeypatch)
    monkeypatch.setattr(model_config_module, "_models_config", model_config_module.ModelsConfig())

    class _MemoryConfig:
        providers = {"file": {}}

        @staticmethod
        def get_provider_settings():
            return {"path": "memory.json"}

    user_doc_path = (tmp_path / "USER.md").resolve()
    user_doc_path.write_text("# User\n", encoding="utf-8")

    monkeypatch.setattr(
        admin_config_service.runtime_config_store,
        "read",
        lambda: {
            "platforms": {"web": True, "weixin": False},
            "features": {"admin": True},
            "cors": {"allowed_origins": ["http://127.0.0.1:8764"]},
        },
    )
    monkeypatch.setattr(admin_config_service, "load_memory_config", lambda: _MemoryConfig())
    monkeypatch.setattr(admin_config_service, "get_memory_provider_name", lambda: "file")
    monkeypatch.setattr(
        admin_config_service,
        "read_managed_env",
        lambda: {
            "ADMIN_USER_IDS": "7",
            "TELEGRAM_BOT_TOKEN": "test-token",
            "WEIXIN_BASE_URL": "https://wx.example.invalid",
            "WEIXIN_CDN_BASE_URL": "https://cdn.example.invalid",
        },
    )
    monkeypatch.setattr(
        admin_config_service.soul_store,
        "load_core",
        lambda: type(
            "SoulPayload",
            (),
            {"path": str((tmp_path / "SOUL.MD").resolve()), "content": "# Soul\n"},
        )(),
    )
    monkeypatch.setattr(admin_config_service, "_admin_user_md_path", lambda admin_id: user_doc_path)

    runtime_snapshot = admin_config_service.build_runtime_config_snapshot(_build_admin_user())
    models_snapshot = admin_config_service.build_models_snapshot()

    assert "models_config" not in runtime_snapshot
    assert runtime_snapshot["paths"]["models"] == str(config_path)
    assert runtime_snapshot["model_status"]["primary"]["model_key"] == "demo/text"
    assert runtime_snapshot["model_status"]["routing"]["model_key"] == "demo/text"
    assert runtime_snapshot["channels"]["web"]["enabled"] is True
    assert runtime_snapshot["channels"]["weixin"]["enabled"] is False
    assert runtime_snapshot["status"]["channels_ready"] is True
    assert models_snapshot["quick_roles"]["primary"]["model_key"] == "demo/text"
    assert models_snapshot["quick_roles"]["routing"]["model_key"] == "demo/text"
    assert models_snapshot["models_config"]["path"] == str(config_path)
    assert models_snapshot["models_config"]["payload"] == payload


def test_apply_models_document_patch_persists_full_document_and_preserves_extra_fields(
    tmp_path, monkeypatch
):
    config_path = (tmp_path / "models.json").resolve()
    monkeypatch.setenv("MODELS_CONFIG_PATH", str(config_path))
    _reset_model_config_state(monkeypatch)
    _redirect_audit_paths(tmp_path)

    payload = _models_payload()
    result = admin_config_service.apply_models_document_patch(payload, actor="tester")
    saved = json.loads(config_path.read_text(encoding="utf-8"))

    assert result["path"] == str(config_path)
    assert saved["customTopLevel"] == {"enabled": True}
    assert saved["providers"]["demo"]["headers"] == {"X-Trace": "demo"}
    assert saved["providers"]["demo"]["models"][0]["temperature"] == 0.2
    assert saved["providers"]["demo"]["models"][0]["output"] == ["text"]
    assert saved["providers"]["demo"]["models"][0]["cost"]["discount"] == 0.5
    assert saved["providers"]["demo"]["models"][0]["limits"]["dailyTokens"] == 120000
    assert saved["providers"]["demo"]["models"][0]["limits"]["burst"] == 2
    assert saved["providers"]["demo"]["models"][1]["labels"] == ["vision"]
    assert saved["providers"]["demo"]["models"][1]["output"] == ["text"]
    assert saved["selection"]["primary"]["strategy"] == "round_robin"
    assert saved["selection"]["primary"]["sticky"] is False
    assert saved["providers"]["demo"]["models"][1]["contextWindow"] == 1000000
    assert saved["providers"]["demo"]["models"][1]["maxTokens"] == 65536
    assert model_config_module.get_configured_model("primary") == "demo/text"
    assert model_config_module.get_configured_model("vision") == "demo/vision"
    assert model_config_module.load_models_config().get_model("demo/text").output == ["text"]
    assert model_config_module.load_models_config().get_model("demo/text").limits.dailyTokens == 120000
    assert model_config_module.load_models_config().get_selection_strategy("primary") == "round_robin"


def test_apply_models_document_patch_rejects_unknown_model_reference(
    tmp_path, monkeypatch
):
    config_path = (tmp_path / "models.json").resolve()
    monkeypatch.setenv("MODELS_CONFIG_PATH", str(config_path))
    _reset_model_config_state(monkeypatch)
    _redirect_audit_paths(tmp_path)

    payload = _models_payload()
    payload["model"]["primary"] = "demo/missing"

    with pytest.raises(HTTPException) as exc:
        admin_config_service.apply_models_document_patch(payload, actor="tester")

    assert exc.value.status_code == 400
    assert "未定义模型" in str(exc.value.detail)


def test_apply_models_document_patch_rejects_image_generation_model_without_image_output(
    tmp_path, monkeypatch
):
    config_path = (tmp_path / "models.json").resolve()
    monkeypatch.setenv("MODELS_CONFIG_PATH", str(config_path))
    _reset_model_config_state(monkeypatch)
    _redirect_audit_paths(tmp_path)

    payload = _models_payload()
    payload["model"]["image_generation"] = "demo/vision"
    payload["models"]["image_generation"] = {"demo/vision": {}}

    with pytest.raises(HTTPException) as exc:
        admin_config_service.apply_models_document_patch(payload, actor="tester")

    assert exc.value.status_code == 400
    assert "必须支持 image 输出" in str(exc.value.detail)


def test_apply_models_document_patch_accepts_image_generation_model_with_image_output(
    tmp_path, monkeypatch
):
    config_path = (tmp_path / "models.json").resolve()
    monkeypatch.setenv("MODELS_CONFIG_PATH", str(config_path))
    _reset_model_config_state(monkeypatch)
    _redirect_audit_paths(tmp_path)

    payload = _models_payload()
    payload["model"]["image_generation"] = "demo/image-gen"
    payload["models"]["image_generation"] = {"demo/image-gen": {}}
    payload["providers"]["demo"]["models"].append(
        {
            "id": "image-gen",
            "name": "Demo Image Gen",
            "reasoning": False,
            "input": ["text"],
            "output": ["image"],
        }
    )

    result = admin_config_service.apply_models_document_patch(payload, actor="tester")
    saved = json.loads(config_path.read_text(encoding="utf-8"))

    assert result["path"] == str(config_path)
    assert saved["model"]["image_generation"] == "demo/image-gen"
    assert saved["providers"]["demo"]["models"][-1]["output"] == ["image"]


def test_apply_runtime_channels_patch_writes_only_credentials_to_env(tmp_path, monkeypatch):
    _redirect_audit_paths(tmp_path)
    runtime_updates = []
    env_updates = {}

    monkeypatch.setattr(
        admin_config_service.runtime_config_store,
        "update_patch",
        lambda patch, actor, reason: runtime_updates.append(
            {"patch": patch, "actor": actor, "reason": reason}
        ),
    )
    monkeypatch.setattr(
        admin_config_service,
        "write_managed_env",
        lambda updates, actor, reason: env_updates.update(updates)
        or {"path": str((tmp_path / ".env").resolve())},
    )
    monkeypatch.setattr(
        admin_config_service,
        "ensure_admin_user_id_present",
        lambda *_args, **_kwargs: None,
    )

    result = admin_config_service.apply_runtime_channels_patch(
        RuntimeChannelsPatch(
            admin_user_ids=["1001"],
            telegram=RuntimeTelegramChannelPatch(enabled=True, bot_token="telegram-token"),
            weixin=RuntimeWeixinChannelPatch(
                enabled=True,
                base_url="https://wx.example.invalid",
                cdn_base_url="https://cdn.example.invalid",
            ),
            web=RuntimeWebChannelPatch(enabled=False),
        ),
        actor="tester",
        current_admin_user_id=42,
    )

    assert result["restart_required"] is True
    assert runtime_updates == [
        {
            "patch": {"platforms": {"telegram": True, "weixin": True, "web": False}},
            "actor": "tester",
            "reason": "runtime_update_platforms",
        }
    ]
    assert env_updates["ADMIN_USER_IDS"] == "1001,42"
    assert env_updates["TELEGRAM_BOT_TOKEN"] == "telegram-token"
    assert env_updates["WEIXIN_BASE_URL"] == "https://wx.example.invalid"
    assert env_updates["WEIXIN_CDN_BASE_URL"] == "https://cdn.example.invalid"
    assert "WEIXIN_ENABLE" not in env_updates
    assert "WEB_CHANNEL_ENABLE" not in env_updates


@pytest.mark.asyncio
async def test_generate_runtime_doc_requires_model_key():
    with pytest.raises(HTTPException) as exc:
        await admin_config_service.generate_runtime_doc(
            RuntimeDocGenerateRequest(kind="soul")
        )

    assert exc.value.status_code == 400
    assert "model_key" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_run_models_latency_check_uses_short_prompt_and_returns_elapsed(
    monkeypatch,
):
    init_kwargs = {}
    request_kwargs = {}
    perf_values = iter([10.0, 10.321])

    class _FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            init_kwargs.update(kwargs)

    async def _fake_create_chat_completion(**kwargs):
        request_kwargs.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="pong"),
                )
            ]
        )

    monkeypatch.setattr(admin_config_service, "AsyncOpenAI", _FakeAsyncOpenAI)
    monkeypatch.setattr(
        admin_config_service,
        "create_chat_completion",
        _fake_create_chat_completion,
    )
    monkeypatch.setattr(
        admin_config_service.time,
        "perf_counter",
        lambda: next(perf_values),
    )

    result = await admin_config_service.run_models_latency_check(
        ModelsLatencyCheckRequest(
            role="routing",
            provider_name="proxy",
            base_url="https://example.invalid/v1",
            api_key="test-key",
            api_style="openai-completions",
            model_id="qwen3.5-flash",
        )
    )

    assert init_kwargs == {
        "api_key": "test-key",
        "base_url": "https://example.invalid/v1",
    }
    assert request_kwargs["session_id"] == "admin-model-latency:routing"
    assert request_kwargs["model"] == "qwen3.5-flash"
    assert request_kwargs["messages"] == [
        {"role": "user", "content": "Reply with pong."}
    ]
    assert request_kwargs["temperature"] == 0
    assert request_kwargs["max_tokens"] == 8
    assert result == {
        "role": "routing",
        "model_key": "proxy/qwen3.5-flash",
        "elapsed_ms": 321,
        "response_preview": "pong",
        "prompt": "Reply with pong.",
    }


@pytest.mark.asyncio
async def test_run_models_latency_check_rejects_unsupported_api_style():
    with pytest.raises(HTTPException) as exc:
        await admin_config_service.run_models_latency_check(
            ModelsLatencyCheckRequest(
                role="routing",
                provider_name="proxy",
                base_url="https://example.invalid/v1",
                api_key="test-key",
                api_style="responses",
                model_id="qwen3.5-flash",
            )
        )

    assert exc.value.status_code == 400
    assert "openai-completions" in str(exc.value.detail)
