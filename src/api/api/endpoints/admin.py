from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from api.auth.models import User
from api.auth.router import require_admin, require_operator
from api.auth.schemas import RuntimeConfigPatch
from api.services.admin_audit import list_admin_audits, record_admin_audit
from api.services.env_config import env_bool, read_managed_env
from core.audit_store import audit_store
from core.config import (
    MODELS_CONFIG_PATH,
)
from core.memory_config import MEMORY_CONFIG_PATH, get_memory_provider_name, load_memory_config, reset_memory_config_cache
from core.model_config import (
    get_configured_model,
    load_models_config,
    normalize_model_role,
    resolve_models_config_path,
    update_configured_model,
)
from core.runtime_config_store import runtime_config_store

router = APIRouter()


def _client_ip(request: Request) -> str:
    forwarded = str(request.headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client is None:
        return ""
    return str(request.client.host or "").strip()


def _actor(user: User) -> str:
    return f"{user.id}:{user.email}"


def _path_exists(path: str) -> bool:
    try:
        return Path(path).expanduser().resolve().exists()
    except Exception:
        return False


def _git_head() -> str:
    head_path = Path(".git/HEAD")
    if not head_path.exists():
        return ""
    try:
        head = head_path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    if head.startswith("ref:"):
        ref = head.split(" ", 1)[1].strip()
        ref_path = Path(".git") / ref
        if ref_path.exists():
            try:
                return ref_path.read_text(encoding="utf-8").strip()
            except Exception:
                return ""
    return head


def _platform_env_summary() -> dict[str, dict[str, Any]]:
    env_values = read_managed_env()
    return {
        "telegram": {
            "configured": bool(str(env_values.get("TELEGRAM_BOT_TOKEN", "")).strip()),
        },
        "discord": {
            "configured": bool(str(env_values.get("DISCORD_BOT_TOKEN", "")).strip()),
        },
        "dingtalk": {
            "configured": bool(
                str(env_values.get("DINGTALK_CLIENT_ID", "")).strip()
                and str(env_values.get("DINGTALK_CLIENT_SECRET", "")).strip()
            ),
        },
        "weixin": {
            "configured": env_bool(env_values.get("WEIXIN_ENABLE"), default=False),
        },
        "web": {
            "configured": env_bool(env_values.get("WEB_CHANNEL_ENABLE"), default=True),
        },
    }


def _redact_settings(payload: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in payload.items():
        normalized_key = str(key or "").strip().lower()
        if any(token in normalized_key for token in ("key", "token", "secret", "password")):
            redacted[key] = bool(str(value or "").strip())
            continue
        if isinstance(value, dict):
            redacted[key] = _redact_settings(value)
            continue
        redacted[key] = value
    return redacted


def _runtime_snapshot() -> dict[str, Any]:
    runtime_payload = runtime_config_store.read()
    models = load_models_config()
    memory = load_memory_config()
    model_roles = {}
    for role in ("primary", "routing", "vision", "image_generation", "voice"):
        model_roles[role] = get_configured_model(role)
    return {
        "runtime_config": runtime_payload,
        "model_roles": model_roles,
        "model_catalog": {
            "all": models.list_models(),
            "pools": {
                pool: models.get_model_pool(pool)
                for pool in ("primary", "routing", "vision", "image_generation", "voice")
            },
        },
        "memory": {
            "provider": get_memory_provider_name(),
            "providers": sorted(memory.providers.keys()),
            "active_settings": _redact_settings(memory.get_provider_settings()),
        },
        "platform_env": _platform_env_summary(),
        "config_files": {
            "models": str(resolve_models_config_path()),
            "models_exists": _path_exists(MODELS_CONFIG_PATH),
            "memory": str(Path(os.getenv("MEMORY_CONFIG_PATH", MEMORY_CONFIG_PATH)).expanduser().resolve()),
            "memory_exists": _path_exists(os.getenv("MEMORY_CONFIG_PATH", MEMORY_CONFIG_PATH)),
            "env_exists": Path(".env").exists(),
        },
        "version": {
            "git_head": _git_head(),
        },
    }


def _update_memory_provider(provider: str, *, actor: str) -> dict[str, Any]:
    normalized_provider = str(provider or "").strip().lower()
    if not normalized_provider:
        raise HTTPException(status_code=400, detail="memory_provider 不能为空")
    config_path = Path(os.getenv("MEMORY_CONFIG_PATH", MEMORY_CONFIG_PATH)).expanduser().resolve()
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"memory 配置损坏: {exc}") from exc
    else:
        data = {"provider": "file", "providers": {"file": {}, "mem0": {}}}
    providers = data.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    providers.setdefault(normalized_provider, {})
    data["providers"] = providers
    data["provider"] = normalized_provider
    audit_store.write_versioned(
        config_path,
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        actor=actor,
        reason="update_memory_provider",
        category="memory_config",
    )
    reset_memory_config_cache()
    return {
        "provider": normalized_provider,
        "config_path": str(config_path),
    }


@router.get("/runtime")
async def get_runtime_snapshot(
    _: User = Depends(require_operator),
):
    return _runtime_snapshot()


@router.patch("/runtime")
async def patch_runtime_snapshot(
    payload: RuntimeConfigPatch,
    request: Request,
    admin_user: User = Depends(require_admin),
):
    changes: list[str] = []
    actor = _actor(admin_user)

    if payload.platforms:
        runtime_config_store.update_patch(
            {"platforms": dict(payload.platforms)},
            actor=actor,
            reason="admin_update_platforms",
        )
        changes.append("platforms")
    if payload.features:
        runtime_config_store.update_patch(
            {"features": dict(payload.features)},
            actor=actor,
            reason="admin_update_features",
        )
        changes.append("features")
    if payload.cors_allowed_origins is not None:
        runtime_config_store.update_patch(
            {"cors": {"allowed_origins": list(payload.cors_allowed_origins)}},
            actor=actor,
            reason="admin_update_cors",
        )
        changes.append("cors")
    if payload.model_roles:
        for raw_role, model_key in payload.model_roles.items():
            normalized_role = normalize_model_role(raw_role)
            if not normalized_role:
                raise HTTPException(status_code=400, detail=f"不支持的模型角色: {raw_role}")
            update_configured_model(
                normalized_role,
                model_key,
                actor=actor,
                reason="admin_update_model_role",
            )
        changes.append("model_roles")
    if payload.memory_provider is not None:
        _update_memory_provider(payload.memory_provider, actor=actor)
        changes.append("memory_provider")

    await record_admin_audit(
        {
            "action": "patch_runtime",
            "actor": actor,
            "target": "runtime",
            "summary": f"updated {', '.join(changes) or 'nothing'}",
            "ip": _client_ip(request),
            "status": "success",
        }
    )
    return _runtime_snapshot()


@router.get("/diagnostics")
async def diagnostics(
    _: User = Depends(require_operator),
):
    snapshot = _runtime_snapshot()
    runtime_config = snapshot.get("runtime_config") or {}
    return {
        "status": "ok",
        "platforms": runtime_config.get("platforms") or {},
        "features": runtime_config.get("features") or {},
        "platform_env": snapshot.get("platform_env") or {},
        "config_files": snapshot.get("config_files") or {},
        "version": snapshot.get("version") or {},
        "memory": snapshot.get("memory") or {},
    }


@router.get("/audit")
async def admin_audit(
    limit: int = 100,
    _: User = Depends(require_operator),
):
    return {
        "items": await list_admin_audits(limit=limit),
    }
