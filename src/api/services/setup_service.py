from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.models import User, UserRole
from api.schemas.setup import (
    SetupAdminProfilePatch,
    SetupChannelsPatch,
    SetupDocsPatch,
    SetupGenerateRequest,
    SetupModelsPatch,
)
from api.services.env_config import (
    ENV_PATH,
    env_bool,
    env_csv_list,
    ensure_admin_user_id_present,
    read_managed_env,
    write_managed_env,
)
from api.services.user_access_sync import sync_user_core_access
from core.audit_store import audit_store
from core.channel_user_store import DEFAULT_USER_MD, channel_user_store
from core.config import get_client_for_model
from core.model_config import (
    get_configured_model,
    get_model_id_for_api,
    load_models_config,
    reload_models_config,
    resolve_models_config_path,
)
from core.runtime_config_store import runtime_config_store
from core.soul_store import DEFAULT_CORE_SOUL, soul_store


SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
DEFAULT_ROLE_INPUTS: dict[str, list[str]] = {
    "primary": ["text", "image", "voice"],
    "routing": ["text"],
}


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _ensure_markdown_file(path: Path, default_content: str) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(default_content.strip() + "\n", encoding="utf-8")


def _admin_user_md_path(admin_user_id: int | str) -> Path:
    profile = channel_user_store.get_profile(
        platform="web",
        platform_user_id=str(admin_user_id),
        is_admin=True,
    )
    path = Path(profile.user_md_path).resolve()
    _ensure_markdown_file(path, DEFAULT_USER_MD)
    return path


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _normalize_input_types(role: str, values: list[str] | None) -> list[str]:
    allowed = {"text", "image", "voice"}
    normalized: list[str] = []
    for value in values or []:
        token = _safe_text(value).lower()
        if token and token in allowed and token not in normalized:
            normalized.append(token)
    if not normalized:
        normalized = list(DEFAULT_ROLE_INPUTS.get(role, ["text"]))
    if role in {"primary", "routing"} and "text" not in normalized:
        normalized.insert(0, "text")
    return normalized


def _normalize_provider_name(value: Any) -> str:
    provider_name = _safe_text(value).lower()
    if not provider_name:
        raise HTTPException(status_code=400, detail="provider_name 不能为空")
    if not SAFE_NAME_RE.match(provider_name):
        raise HTTPException(status_code=400, detail="provider_name 只能包含字母、数字、点、下划线和横线")
    return provider_name


def _normalize_model_id(value: Any) -> str:
    model_id = _safe_text(value)
    if not model_id:
        raise HTTPException(status_code=400, detail="model_id 不能为空")
    return model_id


def _default_models_payload() -> dict[str, Any]:
    return {
        "model": {},
        "models": {},
        "mode": "merge",
        "providers": {},
    }


def _load_models_payload() -> tuple[Path, dict[str, Any]]:
    config_path = resolve_models_config_path()
    if not config_path.exists():
        return config_path, _default_models_payload()
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"models.json 损坏: {exc}") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="models.json 根节点必须是对象")
    return config_path, data


def _role_snapshot(role: str) -> dict[str, Any]:
    role_key = _safe_text(role).lower()
    models = load_models_config()
    model_key = get_configured_model(role_key)
    default_payload = {
        "configured": False,
        "role": role_key,
        "model_key": model_key,
        "provider_name": "",
        "base_url": "",
        "api_key": "",
        "api_style": "openai-completions",
        "model_id": "",
        "display_name": "",
        "reasoning": False,
        "input_types": list(DEFAULT_ROLE_INPUTS.get(role_key, ["text"])),
    }
    if not model_key:
        return default_payload

    provider_name, _, model_id = model_key.partition("/")
    provider = models.providers.get(provider_name)
    model = models.get_model(model_key)
    if provider is None or model is None:
        default_payload.update(
            {
                "configured": True,
                "provider_name": provider_name,
                "model_id": model_id,
                "display_name": model_id,
            }
        )
        return default_payload

    default_payload.update(
        {
            "configured": True,
            "provider_name": provider_name,
            "base_url": provider.baseUrl,
            "api_key": provider.apiKey,
            "api_style": provider.api,
            "model_id": model.id,
            "display_name": model.name,
            "reasoning": bool(model.reasoning),
            "input_types": [str(item) for item in model.input],
        }
    )
    return default_payload


def build_setup_snapshot(admin_user: User) -> dict[str, Any]:
    reload_models_config()
    env_values = read_managed_env()
    admin_user_ids = env_csv_list(env_values.get("ADMIN_USER_IDS", ""))
    soul_payload = soul_store.load_core()
    user_doc_path = _admin_user_md_path(admin_user.id)
    user_doc_content = _read_text(user_doc_path)
    runtime_payload = runtime_config_store.read()
    primary = _role_snapshot("primary")
    routing = _role_snapshot("routing")
    current_admin_id = str(admin_user.id)

    def _role_ready(payload: dict[str, Any]) -> bool:
        return bool(
            _safe_text(payload.get("provider_name"))
            and _safe_text(payload.get("model_id"))
            and _safe_text(payload.get("base_url"))
            and _safe_text(payload.get("api_key"))
        )

    return {
        "admin_user": {
            "id": admin_user.id,
            "email": admin_user.email,
            "username": admin_user.username,
            "display_name": admin_user.display_name,
            "role": admin_user.role,
            "is_superuser": admin_user.is_superuser,
            "current_admin_user_id": current_admin_id,
        },
        "models": {
            "primary": primary,
            "routing": routing,
        },
        "docs": {
            "soul_path": soul_payload.path,
            "soul_content": soul_payload.content,
            "user_path": str(user_doc_path),
            "user_content": user_doc_content,
        },
        "channels": {
            "platforms": dict((runtime_payload.get("platforms") or {})),
            "admin_user_ids": admin_user_ids,
            "telegram_bot_token": env_values.get("TELEGRAM_BOT_TOKEN", ""),
            "discord_bot_token": env_values.get("DISCORD_BOT_TOKEN", ""),
            "dingtalk_client_id": env_values.get("DINGTALK_CLIENT_ID", ""),
            "dingtalk_client_secret": env_values.get("DINGTALK_CLIENT_SECRET", ""),
            "weixin_enable": env_bool(env_values.get("WEIXIN_ENABLE"), default=False),
            "weixin_base_url": env_values.get("WEIXIN_BASE_URL", ""),
            "weixin_cdn_base_url": env_values.get("WEIXIN_CDN_BASE_URL", ""),
            "web_channel_enable": env_bool(env_values.get("WEB_CHANNEL_ENABLE"), default=True),
        },
        "status": {
            "admin_bound": current_admin_id in admin_user_ids,
            "primary_ready": _role_ready(primary),
            "routing_ready": _role_ready(routing),
            "soul_ready": bool(_safe_text(soul_payload.content)),
            "user_ready": bool(_safe_text(user_doc_content)),
        },
        "paths": {
            "env": str(ENV_PATH),
            "models": str(resolve_models_config_path()),
        },
        "restart_notice": "渠道启停、ADMIN_USER_IDS 和渠道凭证写入 .env 后，需要重启 ikaros core；建议同时重启 API 以避免旧进程继续持有启动时环境变量。",
    }


async def apply_admin_profile_patch(
    admin_user: User,
    patch: SetupAdminProfilePatch,
    *,
    session: AsyncSession,
    password_hasher: Any,
    actor: str,
) -> list[str]:
    changes: list[str] = []
    if patch.email is not None:
        next_email = _safe_text(patch.email)
        if not next_email:
            raise HTTPException(status_code=400, detail="管理员邮箱不能为空")
        if next_email != admin_user.email:
            admin_user.email = next_email
            changes.append("email")
    if patch.username is not None:
        next_username = _safe_text(patch.username) or None
        if next_username != admin_user.username:
            admin_user.username = next_username
            changes.append("username")
    if patch.display_name is not None:
        next_display_name = _safe_text(patch.display_name) or None
        if next_display_name != admin_user.display_name:
            admin_user.display_name = next_display_name
            changes.append("display_name")
    if patch.password is not None:
        raw_password = _safe_text(patch.password)
        if raw_password:
            admin_user.hashed_password = password_hasher.hash(raw_password)
            changes.append("password")

    admin_user.role = UserRole.ADMIN
    admin_user.is_superuser = True
    admin_user.is_active = True
    admin_user.is_verified = True
    session.add(admin_user)
    await session.flush()
    await session.refresh(admin_user)
    await sync_user_core_access(admin_user, actor=actor)
    return changes


def _upsert_role_config(data: dict[str, Any], *, role: str, payload: dict[str, Any]) -> str:
    provider_name = _normalize_provider_name(payload.get("provider_name"))
    model_id = _normalize_model_id(payload.get("model_id"))
    model_key = f"{provider_name}/{model_id}"

    providers = data.setdefault("providers", {})
    if not isinstance(providers, dict):
        raise HTTPException(status_code=500, detail="models.json 中 providers 必须是对象")

    provider_entry = providers.get(provider_name)
    if not isinstance(provider_entry, dict):
        provider_entry = {}
    provider_models = provider_entry.get("models")
    if not isinstance(provider_models, list):
        provider_models = []

    existing_model = None
    existing_index = -1
    for index, item in enumerate(provider_models):
        if isinstance(item, dict) and _safe_text(item.get("id")) == model_id:
            existing_model = dict(item)
            existing_index = index
            break

    updated_model = dict(existing_model or {})
    updated_model.update(
        {
            "id": model_id,
            "name": _safe_text(payload.get("display_name")) or model_id,
            "reasoning": bool(payload.get("reasoning")),
            "input": _normalize_input_types(role, payload.get("input_types")),
            "cost": dict(existing_model.get("cost") or {}) if isinstance(existing_model, dict) else {
                "input": 0,
                "output": 0,
                "cacheRead": 0,
                "cacheWrite": 0,
            },
            "contextWindow": int((existing_model or {}).get("contextWindow") or 1000000),
            "maxTokens": int((existing_model or {}).get("maxTokens") or 65536),
        }
    )
    if existing_index >= 0:
        provider_models[existing_index] = updated_model
    else:
        provider_models.append(updated_model)

    provider_entry["baseUrl"] = _safe_text(payload.get("base_url"))
    provider_entry["apiKey"] = _safe_text(payload.get("api_key"))
    provider_entry["api"] = _safe_text(payload.get("api_style")) or "openai-completions"
    provider_entry["models"] = provider_models
    providers[provider_name] = provider_entry

    pools = data.setdefault("models", {})
    if not isinstance(pools, dict):
        raise HTTPException(status_code=500, detail="models.json 中 models 必须是对象")
    role_pool = pools.get(role)
    if isinstance(role_pool, list):
        role_pool = {str(item): {} for item in role_pool if _safe_text(item)}
    if not isinstance(role_pool, dict):
        role_pool = {}
    role_pool.setdefault(model_key, {})
    pools[role] = role_pool

    role_bindings = data.setdefault("model", {})
    if not isinstance(role_bindings, dict):
        raise HTTPException(status_code=500, detail="models.json 中 model 必须是对象")
    role_bindings[role] = model_key
    data.setdefault("mode", "merge")
    return model_key


def apply_models_patch(
    patch: SetupModelsPatch,
    *,
    actor: str,
) -> dict[str, Any]:
    config_path, data = _load_models_payload()
    updated_roles: dict[str, str] = {}
    for role in ("primary", "routing"):
        role_payload = getattr(patch, role)
        if role_payload is None:
            continue
        updated_roles[role] = _upsert_role_config(
            data,
            role=role,
            payload=role_payload.model_dump(),
        )

    if not updated_roles:
        return {
            "config_path": str(config_path),
            "updated_roles": {},
        }

    rendered = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    audit_store.write_versioned(
        config_path,
        rendered,
        actor=actor,
        reason="setup_models",
        category="models_config",
    )
    reload_models_config(str(config_path))
    return {
        "config_path": str(config_path),
        "updated_roles": updated_roles,
    }


def apply_docs_patch(
    patch: SetupDocsPatch,
    *,
    admin_user: User,
    actor: str,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    if patch.soul_content is not None:
        results["soul"] = soul_store.update_core(
            patch.soul_content,
            actor=actor,
            reason="setup_update_soul",
        )
    if patch.user_content is not None:
        user_doc_path = _admin_user_md_path(admin_user.id)
        result = audit_store.write_versioned(
            user_doc_path,
            patch.user_content.strip() + "\n",
            actor=actor,
            reason="setup_update_admin_user_md",
            category="user_doc",
        )
        results["user"] = {
            "path": str(user_doc_path),
            "previous_version_id": str(result.get("previous_version_id", "")),
        }
    return results


def apply_channels_patch(
    patch: SetupChannelsPatch,
    *,
    actor: str,
    current_admin_user_id: int | str,
) -> dict[str, Any]:
    restart_required = False
    env_updates: dict[str, Any] = {}

    if patch.platforms is not None:
        runtime_config_store.update_patch(
            {"platforms": dict(patch.platforms)},
            actor=actor,
            reason="setup_update_platforms",
        )
        restart_required = True

    if patch.admin_user_ids is not None:
        admin_ids = [_safe_text(item) for item in patch.admin_user_ids if _safe_text(item)]
        current_admin_id = str(current_admin_user_id)
        if current_admin_id and current_admin_id not in admin_ids:
            admin_ids.append(current_admin_id)
        env_updates["ADMIN_USER_IDS"] = ",".join(admin_ids)
    else:
        ensure_admin_user_id_present(
            current_admin_user_id,
            actor=actor,
            reason="setup_preserve_admin_user",
        )

    if patch.telegram_bot_token is not None:
        env_updates["TELEGRAM_BOT_TOKEN"] = patch.telegram_bot_token
    if patch.discord_bot_token is not None:
        env_updates["DISCORD_BOT_TOKEN"] = patch.discord_bot_token
    if patch.dingtalk_client_id is not None:
        env_updates["DINGTALK_CLIENT_ID"] = patch.dingtalk_client_id
    if patch.dingtalk_client_secret is not None:
        env_updates["DINGTALK_CLIENT_SECRET"] = patch.dingtalk_client_secret
    if patch.weixin_enable is not None:
        env_updates["WEIXIN_ENABLE"] = patch.weixin_enable
    if patch.weixin_base_url is not None:
        env_updates["WEIXIN_BASE_URL"] = patch.weixin_base_url
    if patch.weixin_cdn_base_url is not None:
        env_updates["WEIXIN_CDN_BASE_URL"] = patch.weixin_cdn_base_url
    if patch.web_channel_enable is not None:
        env_updates["WEB_CHANNEL_ENABLE"] = patch.web_channel_enable

    env_result = None
    if env_updates:
        env_result = write_managed_env(
            env_updates,
            actor=actor,
            reason="setup_update_channels",
        )
        restart_required = True

    return {
        "restart_required": restart_required,
        "env": env_result,
    }


def _extract_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, str):
                chunks.append(item)
                continue
            if isinstance(item, dict):
                if item.get("type") == "text" and item.get("text"):
                    chunks.append(str(item.get("text")))
                    continue
                text_obj = item.get("text")
                if isinstance(text_obj, dict) and text_obj.get("value"):
                    chunks.append(str(text_obj.get("value")))
        return "\n".join(chunk.strip() for chunk in chunks if chunk.strip()).strip()
    return ""


def _strip_markdown_fence(content: str) -> str:
    text = str(content or "").strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return text


async def generate_setup_doc(payload: SetupGenerateRequest) -> dict[str, Any]:
    model_key = _safe_text(payload.model_key) or get_configured_model("primary")
    if not model_key:
        raise HTTPException(status_code=400, detail="请先配置 Primary 模型，再使用 AI 生成文档")
    client = get_client_for_model(model_key, is_async=True)
    if client is None:
        raise HTTPException(status_code=400, detail="当前 Primary 模型没有可用客户端，请检查 base_url / api_key")

    if payload.kind == "soul":
        system_prompt = (
            "你是 Ikaros 初始化助手。请产出一份高质量、可直接落盘的 SOUL.MD。"
            "输出必须是 Markdown 正文，不要加代码块。"
            "内容必须包含：名称、身份、角色定义、性格与语气、交互原则、边界与禁令。"
            "语气要清晰、具体、可执行，避免空话。"
        )
        user_prompt = (
            "请为 Ikaros 生成 SOUL.MD。\n\n"
            f"补充要求：{_safe_text(payload.brief) or '面向中文用户，兼顾生活与工作助手场景。'}\n\n"
            f"当前已有内容：\n{_safe_text(payload.current_content) or '(无)'}"
        )
    else:
        system_prompt = (
            "你是 Ikaros 初始化助手。请产出一份短版、可直接落盘的 USER.md。"
            "输出必须是 Markdown 正文，不要加代码块。"
            "内容必须包含：用户身份、称呼偏好、关系设定、沟通偏好、禁忌或边界。"
            "总长度控制在 180 到 320 个中文字符以内，优先 4 到 8 条短要点，不要写成长文。"
            "如果用户只提供了两三句信息，就只做轻量整理，不要脑补大量背景，不要为了完整性硬凑篇幅。"
            "要写得自然、具体、可长期维护。"
        )
        user_prompt = (
            "请为当前管理员生成 USER.md。\n\n"
            f"补充要求：{_safe_text(payload.brief) or '尽量体现真实身份、偏好和沟通方式，保持简短。'}\n\n"
            f"当前已有内容：\n{_safe_text(payload.current_content) or '(无)'}"
        )

    try:
        request_payload = {
            "model": get_model_id_for_api(model_key),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if payload.kind == "user":
            request_payload["max_tokens"] = 260
        response = await client.chat.completions.create(**request_payload)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"调用模型生成文档失败: {exc}") from exc

    choices = getattr(response, "choices", None) or []
    if not choices:
        raise HTTPException(status_code=502, detail="模型没有返回可用内容")
    message = getattr(choices[0], "message", None)
    content = _strip_markdown_fence(_extract_message_text(getattr(message, "content", "")))
    if not content:
        raise HTTPException(status_code=502, detail="模型返回为空")
    return {
        "kind": payload.kind,
        "model_key": model_key,
        "content": content,
    }
