from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.models import User, UserRole
from api.schemas.admin_config import (
    AdminProfilePatch,
    ModelsLatencyCheckRequest,
    RuntimeChannelsPatch,
    RuntimeDocGenerateRequest,
    RuntimeDocsPatch,
)
from api.services.env_config import (
    ENV_PATH,
    env_csv_list,
    ensure_admin_user_id_present,
    read_managed_env,
    write_managed_env,
)
from api.services.user_access_sync import sync_user_core_access
from core.app_paths import memory_config_path
from core.audit_store import audit_store
from core.channel_user_store import DEFAULT_USER_MD, channel_user_store
from core.config import AsyncOpenAI, get_client_for_model
from core.memory_config import (
    get_memory_provider_name,
    load_memory_config,
    reset_memory_config_cache,
)
from core.model_config import (
    _parse_models_config_data,
    get_configured_model,
    get_model_id_for_api,
    load_models_config,
    normalize_selection_strategy,
    reload_models_config,
    resolve_models_config_path,
)
from core.runtime_config_store import runtime_config_store
from core.soul_store import soul_store
from services.openai_adapter import create_chat_completion, extract_text_from_chat_completion


SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
MANAGED_MODEL_ROLES = ("primary", "routing", "vision", "image_generation", "voice")
LATENCY_CHECK_TIMEOUT_SEC = 20.0
LATENCY_CHECK_PROMPT = "Reply with pong."
DEFAULT_ROLE_INPUTS: dict[str, list[str]] = {
    "primary": ["text", "image", "voice"],
    "routing": ["text"],
}
ALLOWED_MODEL_INPUTS = {"text", "image", "voice"}
ALLOWED_MODEL_OUTPUTS = {"text", "image", "voice", "video"}
ALLOWED_MODEL_SELECTION_STRATEGIES = {"priority", "round_robin", "least_usage"}
ROLE_REQUIRED_INPUTS: dict[str, list[str]] = {
    "primary": ["text"],
    "routing": ["text"],
    "vision": ["image"],
    "voice": ["voice"],
}
ROLE_REQUIRED_OUTPUTS: dict[str, list[str]] = {
    "image_generation": ["image"],
}


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_managed_role_key(value: Any, *, field_name: str) -> str:
    role = _safe_text(value).lower()
    if role not in MANAGED_MODEL_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} 只支持 {', '.join(MANAGED_MODEL_ROLES)}",
        )
    return role


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
    normalized: list[str] = []
    for value in values or []:
        token = _safe_text(value).lower()
        if token and token in ALLOWED_MODEL_INPUTS and token not in normalized:
            normalized.append(token)
    if not normalized:
        normalized = list(DEFAULT_ROLE_INPUTS.get(role, ["text"]))
    if role in {"primary", "routing"} and "text" not in normalized:
        normalized.insert(0, "text")
    return normalized


def _normalize_generic_input_types(values: Any) -> list[str]:
    normalized: list[str] = []
    raw_values = values if isinstance(values, list) else []
    for value in raw_values:
        token = _safe_text(value).lower()
        if token and token in ALLOWED_MODEL_INPUTS and token not in normalized:
            normalized.append(token)
    return normalized or ["text"]


def _normalize_generic_output_types(values: Any) -> list[str]:
    normalized: list[str] = []
    raw_values = values if isinstance(values, list) else []
    for value in raw_values:
        token = _safe_text(value).lower()
        if token and token in ALLOWED_MODEL_OUTPUTS and token not in normalized:
            normalized.append(token)
    return normalized


def _coerce_int(value: Any, *, field_name: str, default: int, minimum: int = 0) -> int:
    if value in {None, ""}:
        return default
    try:
        parsed = int(value)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} 必须是整数") from exc
    return max(minimum, parsed)


def _coerce_float(value: Any, *, field_name: str, default: float = 0.0) -> float:
    if value in {None, ""}:
        return default
    try:
        return float(value)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} 必须是数字") from exc


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
        "selection": {},
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


def _normalize_models_document(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="models_config 必须是对象")

    normalized = {
        key: value
        for key, value in payload.items()
        if str(key).strip()
        and key not in {"mode", "model", "models", "providers", "selection"}
    }
    normalized["mode"] = _safe_text(payload.get("mode")) or "merge"

    raw_model_bindings = payload.get("model") or {}
    if not isinstance(raw_model_bindings, dict):
        raise HTTPException(status_code=400, detail="models_config.model 必须是对象")
    normalized_model_bindings: dict[str, str] = {}
    for raw_role, model_key in raw_model_bindings.items():
        role = _normalize_managed_role_key(
            raw_role,
            field_name=f"models_config.model.{_safe_text(raw_role) or '(empty)'}",
        )
        normalized_model_bindings[role] = _safe_text(model_key)
    normalized["model"] = normalized_model_bindings

    raw_pools = payload.get("models") or {}
    if not isinstance(raw_pools, dict):
        raise HTTPException(status_code=400, detail="models_config.models 必须是对象")
    normalized_pools: dict[str, Any] = {}
    for raw_pool_name, pool_value in raw_pools.items():
        pool_name = _normalize_managed_role_key(
            raw_pool_name,
            field_name=f"models_config.models.{_safe_text(raw_pool_name) or '(empty)'}",
        )
        if isinstance(pool_value, list):
            normalized_pools[pool_name] = [
                model_key
                for item in pool_value
                if (model_key := _safe_text(item))
            ]
            continue
        if isinstance(pool_value, dict):
            normalized_pools[pool_name] = {
                model_key: dict(meta) if isinstance(meta, dict) else {}
                for raw_model_key, meta in pool_value.items()
                if (model_key := _safe_text(raw_model_key))
            }
            continue
        raise HTTPException(
            status_code=400,
            detail=f"models_config.models.{pool_name} 必须是对象或数组",
        )
    normalized["models"] = normalized_pools

    raw_selection = payload.get("selection") or {}
    if not isinstance(raw_selection, dict):
        raise HTTPException(status_code=400, detail="models_config.selection 必须是对象")
    normalized_selection: dict[str, Any] = {}
    for raw_pool_name, raw_selection_value in raw_selection.items():
        safe_pool_name = _normalize_managed_role_key(
            raw_pool_name,
            field_name=f"models_config.selection.{_safe_text(raw_pool_name) or '(empty)'}",
        )
        if isinstance(raw_selection_value, str):
            selection_payload = {"strategy": raw_selection_value}
        elif isinstance(raw_selection_value, dict):
            selection_payload = dict(raw_selection_value)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"models_config.selection.{safe_pool_name} 必须是对象或字符串",
            )
        normalized_strategy = normalize_selection_strategy(
            selection_payload.get("strategy")
        )
        if normalized_strategy not in ALLOWED_MODEL_SELECTION_STRATEGIES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"models_config.selection.{safe_pool_name}.strategy 必须是 "
                    "priority / round_robin / least_usage"
                ),
            )
        normalized_selection[safe_pool_name] = {
            **selection_payload,
            "strategy": normalized_strategy,
        }
    normalized["selection"] = normalized_selection

    raw_providers = payload.get("providers") or {}
    if not isinstance(raw_providers, dict):
        raise HTTPException(status_code=400, detail="models_config.providers 必须是对象")

    normalized_providers: dict[str, Any] = {}
    for raw_provider_name, raw_provider in raw_providers.items():
        provider_name = _normalize_provider_name(raw_provider_name)
        if not isinstance(raw_provider, dict):
            raise HTTPException(
                status_code=400,
                detail=f"models_config.providers.{provider_name} 必须是对象",
            )
        raw_provider_models = raw_provider.get("models") or []
        if not isinstance(raw_provider_models, list):
            raise HTTPException(
                status_code=400,
                detail=f"models_config.providers.{provider_name}.models 必须是数组",
            )
        normalized_provider_models: list[dict[str, Any]] = []
        seen_model_ids: set[str] = set()
        for index, item in enumerate(raw_provider_models):
            if not isinstance(item, dict):
                raise HTTPException(
                    status_code=400,
                    detail=f"models_config.providers.{provider_name}.models[{index}] 必须是对象",
                )
            model_id = _normalize_model_id(item.get("id"))
            if model_id in seen_model_ids:
                raise HTTPException(
                    status_code=400,
                    detail=f"provider {provider_name} 存在重复模型 id: {model_id}",
                )
            seen_model_ids.add(model_id)
            raw_cost = item.get("cost")
            cost_payload = dict(raw_cost) if isinstance(raw_cost, dict) else {}
            normalized_cost = dict(cost_payload)
            normalized_cost.update(
                {
                    "input": _coerce_float(
                        cost_payload.get("input"),
                        field_name=f"providers.{provider_name}.models[{index}].cost.input",
                    ),
                    "output": _coerce_float(
                        cost_payload.get("output"),
                        field_name=f"providers.{provider_name}.models[{index}].cost.output",
                    ),
                    "cacheRead": _coerce_float(
                        cost_payload.get("cacheRead"),
                        field_name=f"providers.{provider_name}.models[{index}].cost.cacheRead",
                    ),
                    "cacheWrite": _coerce_float(
                        cost_payload.get("cacheWrite"),
                        field_name=f"providers.{provider_name}.models[{index}].cost.cacheWrite",
                    ),
                }
            )
            raw_limits = item.get("limits")
            limits_payload = dict(raw_limits) if isinstance(raw_limits, dict) else {}
            normalized_limits = dict(limits_payload)
            normalized_limits.update(
                {
                    "dailyTokens": _coerce_int(
                        limits_payload.get("dailyTokens"),
                        field_name=f"providers.{provider_name}.models[{index}].limits.dailyTokens",
                        default=0,
                        minimum=0,
                    ),
                    "dailyImages": _coerce_int(
                        limits_payload.get("dailyImages"),
                        field_name=f"providers.{provider_name}.models[{index}].limits.dailyImages",
                        default=0,
                        minimum=0,
                    ),
                }
            )
            normalized_model = dict(item)
            normalized_model.update(
                {
                    "id": model_id,
                    "name": _safe_text(item.get("name")) or model_id,
                    "reasoning": bool(item.get("reasoning")),
                    "input": _normalize_generic_input_types(item.get("input")),
                    "output": _normalize_generic_output_types(item.get("output")),
                    "cost": normalized_cost,
                    "limits": normalized_limits,
                    "contextWindow": _coerce_int(
                        item.get("contextWindow"),
                        field_name=f"providers.{provider_name}.models[{index}].contextWindow",
                        default=1000000,
                        minimum=1,
                    ),
                    "maxTokens": _coerce_int(
                        item.get("maxTokens"),
                        field_name=f"providers.{provider_name}.models[{index}].maxTokens",
                        default=65536,
                        minimum=1,
                    ),
                }
            )
            normalized_provider_models.append(
                normalized_model
            )

        normalized_provider = dict(raw_provider)
        normalized_provider.update(
            {
                "baseUrl": _safe_text(raw_provider.get("baseUrl")),
                "apiKey": _safe_text(raw_provider.get("apiKey")),
                "api": _safe_text(raw_provider.get("api")) or "openai-completions",
                "models": normalized_provider_models,
            }
        )
        normalized_providers[provider_name] = normalized_provider
    normalized["providers"] = normalized_providers

    try:
        parsed = _parse_models_config_data(normalized)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"models_config 配置无效: {exc}",
        ) from exc

    available_model_keys = set(parsed.list_models())
    for raw_role, model_key in normalized["model"].items():
        if model_key and model_key not in available_model_keys:
            raise HTTPException(
                status_code=400,
                detail=f"models_config.model.{raw_role} 引用了未定义模型: {model_key}",
            )
        if model_key:
            _validate_role_model_capability(
                raw_role,
                model_key,
                parsed,
                field_name=f"models_config.model.{raw_role}",
            )
    for pool_name, pool_value in normalized["models"].items():
        pool_keys = pool_value if isinstance(pool_value, list) else list(pool_value.keys())
        for model_key in pool_keys:
            if model_key and model_key not in available_model_keys:
                raise HTTPException(
                    status_code=400,
                    detail=f"models_config.models.{pool_name} 引用了未定义模型: {model_key}",
                )
            if model_key:
                _validate_role_model_capability(
                    pool_name,
                    model_key,
                    parsed,
                    field_name=f"models_config.models.{pool_name}",
                )
    return normalized


def _validate_role_model_capability(
    role: str,
    model_key: str,
    parsed: Any,
    *,
    field_name: str,
) -> None:
    model = parsed.get_model(model_key)
    if model is None:
        return

    for input_type in ROLE_REQUIRED_INPUTS.get(role, []):
        if model.supports_input(input_type):
            continue
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} 里的模型 {model_key} 必须支持 {input_type} 输入",
        )

    for output_type in ROLE_REQUIRED_OUTPUTS.get(role, []):
        if not model.output:
            continue
        if model.supports_output(output_type):
            continue
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} 里的模型 {model_key} 必须支持 {output_type} 输出",
        )


def build_models_config_editor_snapshot() -> dict[str, Any]:
    config_path, data = _load_models_payload()
    return {
        "path": str(config_path),
        "exists": config_path.exists(),
        "payload": data,
    }


def apply_models_document_patch(
    payload: dict[str, Any],
    *,
    actor: str,
) -> dict[str, Any]:
    config_path = resolve_models_config_path()
    normalized = _normalize_models_document(payload)
    rendered = json.dumps(normalized, ensure_ascii=False, indent=2) + "\n"
    audit_store.write_versioned(
        config_path,
        rendered,
        actor=actor,
        reason="runtime_update_models_document",
        category="models_config",
    )
    reload_models_config(str(config_path))
    return build_models_config_editor_snapshot()


def _model_role_ready(payload: dict[str, Any]) -> bool:
    return bool(
        _safe_text(payload.get("provider_name"))
        and _safe_text(payload.get("model_id"))
        and _safe_text(payload.get("base_url"))
        and _safe_text(payload.get("api_key"))
    )


def _model_role_editor_snapshot(role: str) -> dict[str, Any]:
    role_key = _normalize_managed_role_key(role, field_name="role")
    models = load_models_config()
    model_key = get_configured_model(role_key)
    default_payload = {
        "configured": False,
        "ready": False,
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
    default_payload["ready"] = _model_role_ready(default_payload)
    return default_payload


def _model_role_status(role: str) -> dict[str, Any]:
    payload = _model_role_editor_snapshot(role)
    return {
        "role": payload["role"],
        "configured": bool(payload["configured"]),
        "ready": bool(payload["ready"]),
        "model_key": payload["model_key"],
        "provider_name": payload["provider_name"],
        "model_id": payload["model_id"],
        "display_name": payload["display_name"],
    }


def build_models_snapshot() -> dict[str, Any]:
    reload_models_config()
    primary = _model_role_editor_snapshot("primary")
    routing = _model_role_editor_snapshot("routing")
    return {
        "quick_roles": {
            "primary": primary,
            "routing": routing,
        },
        "status": {
            "primary_ready": bool(primary["ready"]),
            "routing_ready": bool(routing["ready"]),
        },
        "models_config": build_models_config_editor_snapshot(),
    }


def _preview_text(text: Any, *, limit: int = 120) -> str:
    payload = str(text or "").replace("\r", " ").replace("\n", " ").strip()
    if len(payload) <= limit:
        return payload
    return payload[: max(0, limit - 1)] + "…"


async def run_models_latency_check(
    payload: ModelsLatencyCheckRequest,
) -> dict[str, Any]:
    role = _normalize_managed_role_key(payload.role, field_name="role")
    provider_name = _normalize_provider_name(payload.provider_name)
    model_id = _normalize_model_id(payload.model_id)
    api_style = _safe_text(payload.api_style) or "openai-completions"
    base_url = _safe_text(payload.base_url)
    api_key = str(payload.api_key or "").strip()

    if api_style != "openai-completions":
        raise HTTPException(
            status_code=400,
            detail="延迟测试当前只支持 openai-completions",
        )
    if AsyncOpenAI is None:
        raise HTTPException(status_code=500, detail="OpenAI 客户端不可用")
    if not api_key:
        raise HTTPException(status_code=400, detail="api_key 不能为空")

    client_kwargs: dict[str, Any] = {
        "api_key": api_key,
    }
    if base_url:
        client_kwargs["base_url"] = base_url
    client = AsyncOpenAI(**client_kwargs)

    started = time.perf_counter()
    try:
        response = await asyncio.wait_for(
            create_chat_completion(
                async_client=client,
                session_id=f"admin-model-latency:{role}",
                model=model_id,
                messages=[{"role": "user", "content": LATENCY_CHECK_PROMPT}],
                temperature=0,
                max_tokens=8,
            ),
            timeout=LATENCY_CHECK_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail=f"延迟测试超时（{int(LATENCY_CHECK_TIMEOUT_SEC)} 秒）",
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"延迟测试失败: {exc}") from exc
    finally:
        close_method = getattr(client, "close", None)
        if callable(close_method):
            maybe_coro = close_method()
            if asyncio.iscoroutine(maybe_coro):
                await maybe_coro

    elapsed_ms = max(1, int(round((time.perf_counter() - started) * 1000)))
    response_text = extract_text_from_chat_completion(response)
    if not response_text:
        raise HTTPException(status_code=502, detail="模型返回为空")

    return {
        "role": role,
        "model_key": f"{provider_name}/{model_id}",
        "elapsed_ms": elapsed_ms,
        "response_preview": _preview_text(response_text),
        "prompt": LATENCY_CHECK_PROMPT,
    }


def _channels_ready(payload: dict[str, Any]) -> bool:
    channel_keys = ("telegram", "discord", "dingtalk", "weixin", "web")
    enabled_channels = [
        payload[key]
        for key in channel_keys
        if isinstance(payload.get(key), dict) and bool(payload[key].get("enabled"))
    ]
    if not enabled_channels:
        return False
    return all(bool(channel.get("configured")) for channel in enabled_channels)


def build_runtime_config_snapshot(admin_user: User) -> dict[str, Any]:
    reload_models_config()
    env_values = read_managed_env()
    admin_user_ids = env_csv_list(env_values.get("ADMIN_USER_IDS", ""))
    soul_payload = soul_store.load_core()
    user_doc_path = _admin_user_md_path(admin_user.id)
    user_doc_content = _read_text(user_doc_path)
    runtime_payload = runtime_config_store.read()
    features = dict(runtime_payload.get("features") or {})
    cors_allowed_origins = list((runtime_payload.get("cors") or {}).get("allowed_origins") or [])
    memory = load_memory_config()
    primary = _model_role_status("primary")
    routing = _model_role_status("routing")
    current_admin_id = str(admin_user.id)
    platforms = dict(runtime_payload.get("platforms") or {})
    channels = {
        "admin_user_ids": admin_user_ids,
        "telegram": {
            "enabled": bool(platforms.get("telegram")),
            "configured": bool(_safe_text(env_values.get("TELEGRAM_BOT_TOKEN"))),
            "bot_token": env_values.get("TELEGRAM_BOT_TOKEN", ""),
        },
        "discord": {
            "enabled": bool(platforms.get("discord")),
            "configured": bool(_safe_text(env_values.get("DISCORD_BOT_TOKEN"))),
            "bot_token": env_values.get("DISCORD_BOT_TOKEN", ""),
        },
        "dingtalk": {
            "enabled": bool(platforms.get("dingtalk")),
            "configured": bool(
                _safe_text(env_values.get("DINGTALK_CLIENT_ID"))
                and _safe_text(env_values.get("DINGTALK_CLIENT_SECRET"))
            ),
            "client_id": env_values.get("DINGTALK_CLIENT_ID", ""),
            "client_secret": env_values.get("DINGTALK_CLIENT_SECRET", ""),
        },
        "weixin": {
            "enabled": bool(platforms.get("weixin")),
            "configured": bool(
                _safe_text(env_values.get("WEIXIN_BASE_URL"))
                and _safe_text(env_values.get("WEIXIN_CDN_BASE_URL"))
            ),
            "base_url": env_values.get("WEIXIN_BASE_URL", ""),
            "cdn_base_url": env_values.get("WEIXIN_CDN_BASE_URL", ""),
        },
        "web": {
            "enabled": bool(platforms.get("web")),
            "configured": True,
        },
    }

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
        "model_status": {
            "primary": primary,
            "routing": routing,
        },
        "docs": {
            "soul_path": soul_payload.path,
            "soul_content": soul_payload.content,
            "user_path": str(user_doc_path),
            "user_content": user_doc_content,
        },
        "channels": channels,
        "features": features,
        "cors_allowed_origins": cors_allowed_origins,
        "memory": {
            "provider": get_memory_provider_name(),
            "providers": sorted(memory.providers.keys()),
            "active_settings": _redact_settings(memory.get_provider_settings()),
        },
        "status": {
            "admin_bound": current_admin_id in admin_user_ids,
            "primary_ready": bool(primary["ready"]),
            "routing_ready": bool(routing["ready"]),
            "soul_ready": bool(_safe_text(soul_payload.content)),
            "user_ready": bool(_safe_text(user_doc_content)),
            "channels_ready": _channels_ready(channels),
        },
        "paths": {
            "env": str(ENV_PATH),
            "models": str(resolve_models_config_path()),
            "memory": str(memory_config_path()),
        },
        "restart_notice": "ADMIN_USER_IDS 和渠道凭证写入 .env 后，需要重启 ikaros core；建议同时重启 API 以避免旧进程继续持有启动时环境变量。",
    }


async def apply_admin_profile_patch(
    admin_user: User,
    patch: AdminProfilePatch,
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


def apply_runtime_docs_patch(
    patch: RuntimeDocsPatch,
    *,
    admin_user: User,
    actor: str,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    if patch.soul_content is not None:
        results["soul"] = soul_store.update_core(
            patch.soul_content,
            actor=actor,
            reason="runtime_update_soul",
        )
    if patch.user_content is not None:
        user_doc_path = _admin_user_md_path(admin_user.id)
        result = audit_store.write_versioned(
            user_doc_path,
            patch.user_content.strip() + "\n",
            actor=actor,
            reason="runtime_update_admin_user_md",
            category="user_doc",
        )
        results["user"] = {
            "path": str(user_doc_path),
            "previous_version_id": str(result.get("previous_version_id", "")),
        }
    return results


def apply_runtime_channels_patch(
    patch: RuntimeChannelsPatch,
    *,
    actor: str,
    current_admin_user_id: int | str,
) -> dict[str, Any]:
    restart_required = False
    env_updates: dict[str, Any] = {}
    platform_updates: dict[str, bool] = {}

    if patch.telegram is not None:
        if patch.telegram.enabled is not None:
            platform_updates["telegram"] = bool(patch.telegram.enabled)
        if patch.telegram.bot_token is not None:
            env_updates["TELEGRAM_BOT_TOKEN"] = patch.telegram.bot_token
    if patch.discord is not None:
        if patch.discord.enabled is not None:
            platform_updates["discord"] = bool(patch.discord.enabled)
        if patch.discord.bot_token is not None:
            env_updates["DISCORD_BOT_TOKEN"] = patch.discord.bot_token
    if patch.dingtalk is not None:
        if patch.dingtalk.enabled is not None:
            platform_updates["dingtalk"] = bool(patch.dingtalk.enabled)
        if patch.dingtalk.client_id is not None:
            env_updates["DINGTALK_CLIENT_ID"] = patch.dingtalk.client_id
        if patch.dingtalk.client_secret is not None:
            env_updates["DINGTALK_CLIENT_SECRET"] = patch.dingtalk.client_secret
    if patch.weixin is not None:
        if patch.weixin.enabled is not None:
            platform_updates["weixin"] = bool(patch.weixin.enabled)
        if patch.weixin.base_url is not None:
            env_updates["WEIXIN_BASE_URL"] = patch.weixin.base_url
        if patch.weixin.cdn_base_url is not None:
            env_updates["WEIXIN_CDN_BASE_URL"] = patch.weixin.cdn_base_url
    if patch.web is not None and patch.web.enabled is not None:
        platform_updates["web"] = bool(patch.web.enabled)

    if platform_updates:
        runtime_config_store.update_patch(
            {"platforms": platform_updates},
            actor=actor,
            reason="runtime_update_platforms",
        )

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
            reason="runtime_preserve_admin_user",
        )

    env_result = None
    if env_updates:
        env_result = write_managed_env(
            env_updates,
            actor=actor,
            reason="runtime_update_channels",
        )
        restart_required = True

    return {
        "restart_required": restart_required,
        "env": env_result,
    }


def apply_runtime_settings_patch(
    *,
    features: dict[str, bool] | None,
    cors_allowed_origins: list[str] | None,
    actor: str,
) -> list[str]:
    changes: list[str] = []
    if features is not None:
        runtime_config_store.update_patch(
            {"features": dict(features)},
            actor=actor,
            reason="runtime_update_features",
        )
        changes.append("features")
    if cors_allowed_origins is not None:
        runtime_config_store.update_patch(
            {"cors": {"allowed_origins": list(cors_allowed_origins)}},
            actor=actor,
            reason="runtime_update_cors",
        )
        changes.append("cors")
    return changes


def update_memory_provider(provider: str, *, actor: str) -> dict[str, Any]:
    normalized_provider = _safe_text(provider).lower()
    if not normalized_provider:
        raise HTTPException(status_code=400, detail="memory_provider 不能为空")
    config_path = memory_config_path()
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


async def generate_runtime_doc(payload: RuntimeDocGenerateRequest) -> dict[str, Any]:
    model_key = _safe_text(payload.model_key)
    if not model_key:
        raise HTTPException(status_code=400, detail="model_key 不能为空")
    client = get_client_for_model(model_key, is_async=True)
    if client is None:
        raise HTTPException(status_code=400, detail="当前模型没有可用客户端，请检查 base_url / api_key")

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
        response = await create_chat_completion(
            async_client=client,
            **request_payload,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"调用模型生成文档失败: {exc}") from exc

    content = _strip_markdown_fence(extract_text_from_chat_completion(response))
    if not content:
        raise HTTPException(status_code=502, detail="模型返回为空")
    return {
        "kind": payload.kind,
        "model_key": model_key,
        "content": content,
    }
