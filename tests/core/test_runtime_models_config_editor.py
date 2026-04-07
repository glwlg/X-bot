import json
from pathlib import Path

import pytest
from fastapi import HTTPException

import core.model_config as model_config_module
from api.api.endpoints import admin as admin_endpoint
from api.services import setup_service
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


def test_runtime_snapshot_reloads_current_models_config_and_includes_editor_payload(
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

    monkeypatch.setattr(
        admin_endpoint.runtime_config_store,
        "read",
        lambda: {
            "platforms": {"web": True},
            "features": {"admin": True},
            "cors": {"allowed_origins": ["http://127.0.0.1:8764"]},
        },
    )
    monkeypatch.setattr(admin_endpoint, "load_memory_config", lambda: _MemoryConfig())
    monkeypatch.setattr(admin_endpoint, "get_memory_provider_name", lambda: "file")
    monkeypatch.setattr(admin_endpoint, "_platform_env_summary", lambda: {"web": {"configured": True}})
    monkeypatch.setattr(admin_endpoint, "_git_head", lambda: "deadbeef")
    monkeypatch.setattr(admin_endpoint, "memory_config_path", lambda: (tmp_path / "memory.json").resolve())
    monkeypatch.setattr(admin_endpoint, "env_path", lambda: (tmp_path / ".env").resolve())

    snapshot = admin_endpoint._runtime_snapshot(include_models_config=True)

    assert snapshot["config_files"]["models"] == str(config_path)
    assert snapshot["model_roles"]["primary"] == "demo/text"
    assert snapshot["model_catalog"]["all"] == ["demo/text", "demo/vision"]
    assert snapshot["model_catalog"]["pools"]["vision"] == ["demo/vision"]
    assert snapshot["models_config"]["path"] == str(config_path)
    assert snapshot["models_config"]["payload"] == payload


def test_apply_models_document_patch_persists_full_document_and_preserves_extra_fields(
    tmp_path, monkeypatch
):
    config_path = (tmp_path / "models.json").resolve()
    monkeypatch.setenv("MODELS_CONFIG_PATH", str(config_path))
    _reset_model_config_state(monkeypatch)
    _redirect_audit_paths(tmp_path)

    payload = _models_payload()
    result = setup_service.apply_models_document_patch(payload, actor="tester")
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
        setup_service.apply_models_document_patch(payload, actor="tester")

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
        setup_service.apply_models_document_patch(payload, actor="tester")

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

    result = setup_service.apply_models_document_patch(payload, actor="tester")
    saved = json.loads(config_path.read_text(encoding="utf-8"))

    assert result["path"] == str(config_path)
    assert saved["model"]["image_generation"] == "demo/image-gen"
    assert saved["providers"]["demo"]["models"][-1]["output"] == ["image"]
