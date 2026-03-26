from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.models import User
from api.auth.router import require_admin
from api.auth.users import get_user_manager
from api.core.database import get_async_session
from api.schemas.setup import SetupGenerateRequest, SetupPatchRequest
from api.services.admin_audit import record_admin_audit
from api.services.setup_service import (
    apply_admin_profile_patch,
    apply_channels_patch,
    apply_docs_patch,
    apply_models_patch,
    build_setup_snapshot,
    generate_setup_doc,
)


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


@router.get("/setup")
async def get_setup_snapshot(
    admin_user: User = Depends(require_admin),
):
    return build_setup_snapshot(admin_user)


@router.patch("/setup")
async def patch_setup(
    payload: SetupPatchRequest,
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

    if payload.models is not None:
        apply_models_patch(payload.models, actor=actor)
        changed_sections.append("models")

    if payload.docs is not None:
        apply_docs_patch(payload.docs, admin_user=admin_user, actor=actor)
        changed_sections.append("docs")

    if payload.channels is not None:
        channel_result = apply_channels_patch(
            payload.channels,
            actor=actor,
            current_admin_user_id=admin_user.id,
        )
        restart_required = bool(channel_result.get("restart_required"))
        changed_sections.append("channels")

    await record_admin_audit(
        {
            "action": "patch_setup",
            "actor": actor,
            "target": "setup",
            "summary": f"updated sections: {', '.join(changed_sections) or 'nothing'}",
            "ip": _client_ip(request),
            "status": "success",
        }
    )

    return {
        "snapshot": build_setup_snapshot(admin_user),
        "changed_sections": changed_sections,
        "restart_required": restart_required,
    }


@router.post("/setup/generate")
async def generate_setup_markdown(
    payload: SetupGenerateRequest,
    request: Request,
    admin_user: User = Depends(require_admin),
):
    result = await generate_setup_doc(payload)
    await record_admin_audit(
        {
            "action": "generate_setup_doc",
            "actor": _actor(admin_user),
            "target": f"setup:{result.get('kind')}",
            "summary": f"generated {result.get('kind')} via {result.get('model_key')}",
            "ip": _client_ip(request),
            "status": "success",
        }
    )
    return result

