from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.models import User
from api.auth.router import require_admin, require_operator
from api.auth.users import get_user_manager
from api.core.database import get_async_session
from api.schemas.admin_config import (
    ModelsConfigPatchRequest,
    RuntimeConfigPatchRequest,
    RuntimeDocGenerateRequest,
)
from api.services.admin_audit import list_admin_audits, record_admin_audit
from api.services.admin_config_service import (
    apply_admin_profile_patch,
    apply_models_document_patch,
    apply_runtime_channels_patch,
    apply_runtime_docs_patch,
    apply_runtime_settings_patch,
    build_models_snapshot,
    build_runtime_config_snapshot,
    generate_runtime_doc,
    update_memory_provider,
)
from api.services.env_config import read_managed_env
from core.app_paths import env_path, memory_config_path
from core.memory_config import get_memory_provider_name, load_memory_config
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
            "configured": bool(
                str(env_values.get("WEIXIN_BASE_URL", "")).strip()
                and str(env_values.get("WEIXIN_CDN_BASE_URL", "")).strip()
            ),
        },
        "web": {
            "configured": True,
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


def _diagnostics_snapshot() -> dict[str, Any]:
    runtime_payload = runtime_config_store.read()
    memory = load_memory_config()
    return {
        "runtime_config": runtime_payload,
        "platform_env": _platform_env_summary(),
        "config_files": {
            "memory": str(memory_config_path()),
            "memory_exists": _path_exists(str(memory_config_path())),
            "env_exists": env_path().exists(),
        },
        "version": {
            "git_head": _git_head(),
        },
        "memory": {
            "provider": get_memory_provider_name(),
            "providers": sorted(memory.providers.keys()),
            "active_settings": _redact_settings(memory.get_provider_settings()),
        },
    }


@router.get("/runtime")
async def get_runtime_snapshot(
    admin_user: User = Depends(require_admin),
):
    return build_runtime_config_snapshot(admin_user)


@router.patch("/runtime")
async def patch_runtime_snapshot(
    payload: RuntimeConfigPatchRequest,
    request: Request,
    admin_user: User = Depends(require_admin),
    manager=Depends(get_user_manager),
    session: AsyncSession = Depends(get_async_session),
):
    actor = _actor(admin_user)
    changed_sections: list[str] = []
    restart_required = False

    if payload.admin_user is not None:
        if payload.admin_user.email is not None:
            next_email = str(payload.admin_user.email or "").strip()
            existing = await session.execute(
                select(User).where(User.email == next_email, User.id != admin_user.id)
            )
            if existing.unique().scalar_one_or_none() is not None:
                raise HTTPException(status_code=400, detail="该邮箱已被其他用户占用")
        if payload.admin_user.username is not None:
            next_username = str(payload.admin_user.username or "").strip()
            if next_username:
                existing = await session.execute(
                    select(User).where(User.username == next_username, User.id != admin_user.id)
                )
                if existing.unique().scalar_one_or_none() is not None:
                    raise HTTPException(status_code=400, detail="该用户名已被其他用户占用")
        await apply_admin_profile_patch(
            admin_user,
            payload.admin_user,
            session=session,
            password_hasher=manager.password_helper,
            actor=actor,
        )
        changed_sections.append("admin_user")

    if payload.docs is not None:
        apply_runtime_docs_patch(payload.docs, admin_user=admin_user, actor=actor)
        changed_sections.append("docs")

    if payload.channels is not None:
        channel_result = apply_runtime_channels_patch(
            payload.channels,
            actor=actor,
            current_admin_user_id=admin_user.id,
        )
        restart_required = bool(channel_result.get("restart_required"))
        changed_sections.append("channels")

    runtime_setting_changes = apply_runtime_settings_patch(
        features=payload.features,
        cors_allowed_origins=payload.cors_allowed_origins,
        actor=actor,
    )
    changed_sections.extend(runtime_setting_changes)

    if payload.memory_provider is not None:
        update_memory_provider(payload.memory_provider, actor=actor)
        changed_sections.append("memory_provider")

    await record_admin_audit(
        {
            "action": "patch_runtime_config",
            "actor": actor,
            "target": "runtime",
            "summary": f"updated {', '.join(changed_sections) or 'nothing'}",
            "ip": _client_ip(request),
            "status": "success",
        }
    )
    return {
        "snapshot": build_runtime_config_snapshot(admin_user),
        "changed_sections": changed_sections,
        "restart_required": restart_required,
    }


@router.post("/runtime/generate-doc")
async def generate_runtime_markdown(
    payload: RuntimeDocGenerateRequest,
    request: Request,
    admin_user: User = Depends(require_admin),
):
    result = await generate_runtime_doc(payload)
    await record_admin_audit(
        {
            "action": "generate_runtime_doc",
            "actor": _actor(admin_user),
            "target": f"runtime:{result.get('kind')}",
            "summary": f"generated {result.get('kind')} via {result.get('model_key')}",
            "ip": _client_ip(request),
            "status": "success",
        }
    )
    return result


@router.get("/models")
async def get_models_snapshot(
    _: User = Depends(require_admin),
):
    return build_models_snapshot()


@router.patch("/models")
async def patch_models_snapshot(
    payload: ModelsConfigPatchRequest,
    request: Request,
    admin_user: User = Depends(require_admin),
):
    actor = _actor(admin_user)
    apply_models_document_patch(dict(payload.models_config), actor=actor)
    await record_admin_audit(
        {
            "action": "patch_models_config",
            "actor": actor,
            "target": "models",
            "summary": "updated models_config",
            "ip": _client_ip(request),
            "status": "success",
        }
    )
    return {
        "snapshot": build_models_snapshot(),
    }


@router.get("/diagnostics")
async def diagnostics(
    _: User = Depends(require_operator),
):
    snapshot = _diagnostics_snapshot()
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
