from __future__ import annotations

import json
from typing import Any

from core.model_config import (
    get_configured_model,
    get_current_model,
    normalize_model_role,
    reload_models_config,
    resolve_models_config_path,
    update_configured_model,
)
from core.platform.models import UnifiedContext

from .base_handlers import check_permission_unified, edit_callback_message

_ROLE_ORDER = [
    "primary",
    "routing",
    "vision",
    "image_generation",
    "voice",
]

_ROLE_LABELS = {
    "primary": "主对话",
    "routing": "路由",
    "vision": "视觉",
    "image_generation": "生图",
    "voice": "语音",
}

_MODELS_PER_PAGE = 6
_MENU_STATE_KEY = "_model_menu_items"


def _parse_subcommand(text: str) -> tuple[str, str]:
    raw = (text or "").strip()
    if not raw:
        return "show", ""
    parts = raw.split(maxsplit=2)
    if not parts:
        return "show", ""
    if not parts[0].startswith("/model"):
        return "show", ""
    if len(parts) == 1:
        return "show", ""
    cmd = parts[1].strip().lower()
    args = parts[2].strip() if len(parts) >= 3 else ""
    return cmd, args


def _model_usage_text() -> str:
    return (
        "用法:\n"
        "`/model`\n"
        "`/model show`\n"
        "`/model list`\n"
        "`/model list <primary|routing|vision|image_generation|voice>`\n"
        "`/model use <provider/model>`\n"
        "`/model use <role> <provider/model>`\n"
        "`/model help`\n\n"
        "示例:\n"
        "`/model use proxy/qwen3.5-flash`\n"
        "`/model use primary proxy/qwen3.5-flash`\n"
        "`/model use vision proxy/gpt-5.4`"
    )


def _display_role(role: str) -> str:
    normalized = normalize_model_role(role)
    if not normalized:
        return role
    return f"{_ROLE_LABELS.get(normalized, normalized)}({normalized})"


def _configured_roles() -> dict[str, str]:
    return {role: get_configured_model(role) for role in _ROLE_ORDER}


def _menu_state(ctx: UnifiedContext) -> dict[str, list[str]]:
    user_data = ctx.user_data
    state = user_data.get(_MENU_STATE_KEY)
    if not isinstance(state, dict):
        state = {}
        user_data[_MENU_STATE_KEY] = state
    return state


def _get_role_models(config: Any, role: str) -> list[str]:
    normalized_role = normalize_model_role(role)
    if not normalized_role:
        return []
    return config.get_model_pool(normalized_role) or config.list_models()


def _cache_role_models(ctx: UnifiedContext, role: str, models: list[str]) -> None:
    _menu_state(ctx)[role] = list(models)


def _resolve_cached_role_models(
    ctx: UnifiedContext, config: Any, role: str
) -> list[str]:
    normalized_role = normalize_model_role(role)
    if not normalized_role:
        return []
    cached = _menu_state(ctx).get(normalized_role)
    if isinstance(cached, list) and cached:
        return [str(item) for item in cached if str(item).strip()]
    models = _get_role_models(config, normalized_role)
    _cache_role_models(ctx, normalized_role, models)
    return models


def _short_model_label(config: Any, model_key: str, *, selected: bool = False) -> str:
    model_config = config.get_model(model_key)
    label = ""
    if model_config is not None:
        label = str(model_config.name or "").strip()
    if not label and "/" in model_key:
        label = model_key.split("/", 1)[1]
    if not label:
        label = model_key
    if len(label) > 28:
        label = label[:25] + "..."
    if selected:
        return f"✅ {label}"
    return label


def _home_ui() -> dict[str, Any]:
    return {
        "actions": [
            [
                {"text": "主对话", "callback_data": "model_role:primary:0"},
                {"text": "路由", "callback_data": "model_role:routing:0"},
            ],
            [
                {"text": "视觉", "callback_data": "model_role:vision:0"},
                {"text": "生图", "callback_data": "model_role:image_generation:0"},
            ],
            [
                {"text": "语音", "callback_data": "model_role:voice:0"},
                {"text": "全部模型", "callback_data": "model_all"},
            ],
            [
                {"text": "刷新", "callback_data": "model_home"},
            ],
        ]
    }


def _build_summary_payload(prefix: str = "") -> tuple[str, dict[str, Any]]:
    config = reload_models_config()
    current_model = get_current_model() or "未配置"
    config_path = resolve_models_config_path()
    selected = _configured_roles()

    lines: list[str] = []
    if prefix:
        lines.extend([prefix.strip(), ""])
    lines.extend(
        [
            "🤖 当前模型配置",
            "",
            f"配置文件：`{config_path}`",
            f"运行中 primary：`{current_model}`",
            f"已定义模型数：`{len(config.list_models())}`",
            "",
        ]
    )
    for role in _ROLE_ORDER:
        value = selected.get(role) or "未配置"
        lines.append(f"- {_display_role(role)}：`{value}`")
    lines.extend(["", "点击下方按钮选择要切换的角色。"])
    return "\n".join(lines), _home_ui()


def _build_all_models_payload() -> tuple[str, dict[str, Any]]:
    config = reload_models_config()
    current_model = get_current_model()
    selected = _configured_roles()

    lines = ["🤖 已定义模型列表"]
    for model_key in config.list_models():
        model_config = config.get_model(model_key)
        tags: list[str] = []
        selected_roles = [
            normalized
            for normalized, configured_model in selected.items()
            if configured_model == model_key
        ]
        if selected_roles:
            tags.append(
                "selected="
                + "/".join(_ROLE_LABELS.get(role, role) for role in selected_roles)
            )
        if current_model and model_key == current_model:
            tags.append("runtime")

        metadata: list[str] = []
        if model_config is not None:
            input_types = ",".join(model_config.input) or "-"
            metadata.append(f"input={input_types}")
            metadata.append("reasoning" if model_config.reasoning else "standard")

        suffix_bits = metadata + tags
        suffix = f" | {' | '.join(suffix_bits)}" if suffix_bits else ""
        lines.append(f"- `{model_key}`{suffix}")

    lines.extend(["", "点下方按钮按角色进入选择菜单。"])
    return "\n".join(lines), _home_ui()


def _build_role_payload(
    ctx: UnifiedContext, role: str, page: int = 0
) -> tuple[str, dict[str, Any]]:
    config = reload_models_config()
    normalized_role = normalize_model_role(role)
    if not normalized_role:
        return _build_summary_payload("❌ 不支持的模型角色。")

    models = _get_role_models(config, normalized_role)
    _cache_role_models(ctx, normalized_role, models)

    if not models:
        return _build_summary_payload(f"❌ {_display_role(normalized_role)} 没有可选模型。")

    total_pages = (len(models) + _MODELS_PER_PAGE - 1) // _MODELS_PER_PAGE
    page = max(0, min(page, max(0, total_pages - 1)))
    start = page * _MODELS_PER_PAGE
    end = min(len(models), start + _MODELS_PER_PAGE)
    current_value = get_configured_model(normalized_role)

    lines = [
        f"🤖 选择 {_display_role(normalized_role)} 模型",
        "",
        f"当前配置：`{current_value or '未配置'}`",
        f"第 `{page + 1}/{max(1, total_pages)}` 页，共 `{len(models)}` 个候选",
        "",
        "点击下方按钮直接切换：",
    ]
    for model_key in models[start:end]:
        marker = "✅ " if model_key == current_value else ""
        lines.append(f"- {marker}`{model_key}`")

    actions: list[list[dict[str, str]]] = []
    for absolute_index, model_key in enumerate(models[start:end], start=start):
        actions.append(
            [
                {
                    "text": _short_model_label(
                        config,
                        model_key,
                        selected=model_key == current_value,
                    ),
                    "callback_data": f"model_set:{normalized_role}:{absolute_index}",
                }
            ]
        )

    nav_row: list[dict[str, str]] = []
    if page > 0:
        nav_row.append(
            {
                "text": "上一页",
                "callback_data": f"model_role:{normalized_role}:{page - 1}",
            }
        )
    if end < len(models):
        nav_row.append(
            {
                "text": "下一页",
                "callback_data": f"model_role:{normalized_role}:{page + 1}",
            }
        )
    if nav_row:
        actions.append(nav_row)
    actions.append([{"text": "返回总览", "callback_data": "model_home"}])
    return "\n".join(lines), {"actions": actions}


def _parse_use_args(args: str) -> tuple[str, str]:
    parts = str(args or "").strip().split(maxsplit=1)
    if not parts:
        return "", ""
    if len(parts) == 1:
        return "primary", parts[0].strip()
    normalized_role = normalize_model_role(parts[0].strip())
    if normalized_role:
        return normalized_role, parts[1].strip()
    return "", ""


async def model_command(ctx: UnifiedContext) -> None:
    if not await check_permission_unified(ctx):
        return

    text = getattr(ctx.message, "text", "") or ""
    sub, args = _parse_subcommand(text)

    if sub in {"help", "h", "?"}:
        await ctx.reply(_model_usage_text())
        return

    if sub in {"show", "info", "current", "status"}:
        summary_text, summary_ui = _build_summary_payload()
        await ctx.reply(summary_text, ui=summary_ui)
        return

    if sub in {"list", "ls"}:
        role = args.strip()
        if role and not normalize_model_role(role):
            await ctx.reply(
                f"不支持的模型角色：`{role}`\n\n{_model_usage_text()}"
            )
            return
        if role:
            list_text, list_ui = _build_role_payload(ctx, role, page=0)
        else:
            list_text, list_ui = _build_all_models_payload()
        await ctx.reply(list_text, ui=list_ui)
        return

    if sub in {"use", "set", "switch"}:
        role, model_key = _parse_use_args(args)
        normalized_role = normalize_model_role(role) or "primary"
        if not normalized_role or not model_key:
            await ctx.reply(
                "用法: `/model use <provider/model>` 或 `/model use <primary|routing|vision|image_generation|voice> <provider/model>`"
            )
            return

        try:
            result = update_configured_model(normalized_role, model_key)
        except FileNotFoundError:
            await ctx.reply(
                f"❌ 找不到模型配置文件：`{resolve_models_config_path()}`"
            )
            return
        except json.JSONDecodeError as exc:
            await ctx.reply(f"❌ 模型配置文件不是合法 JSON：`{exc}`")
            return
        except ValueError as exc:
            await ctx.reply(f"❌ {exc}")
            return

        current_model = get_current_model() or "未配置"
        success_text, success_ui = _build_summary_payload(
            "✅ 模型配置已更新\n\n"
            f"- 角色：`{result['role']}`\n"
            f"- 原值：`{result['previous'] or '未配置'}`\n"
            f"- 新值：`{result['current']}`\n"
            f"- 配置键：`{result['storage_key']}`\n"
            f"- 配置文件：`{result['config_path']}`\n"
            f"- 运行中 primary：`{current_model}`"
        )
        await ctx.reply(success_text, ui=success_ui)
        return

    await ctx.reply(_model_usage_text())


async def handle_model_callback(ctx: UnifiedContext) -> None:
    data = ctx.callback_data
    if not data:
        return

    message_id = getattr(ctx.message, "id", "") or "model-menu"

    if data == "model_home":
        text, ui = _build_summary_payload()
        await edit_callback_message(ctx, text, ui=ui, message_id=message_id)
        return

    if data == "model_all":
        text, ui = _build_all_models_payload()
        await edit_callback_message(ctx, text, ui=ui, message_id=message_id)
        return

    if data.startswith("model_role:"):
        parts = data.split(":")
        if len(parts) != 3:
            text, ui = _build_summary_payload("❌ 菜单参数无效，请重新选择。")
            await edit_callback_message(ctx, text, ui=ui, message_id=message_id)
            return
        _, role, raw_page = parts
        try:
            page = int(raw_page)
        except Exception:
            page = 0
        text, ui = _build_role_payload(ctx, role, page=page)
        await edit_callback_message(ctx, text, ui=ui, message_id=message_id)
        return

    if data.startswith("model_set:"):
        parts = data.split(":")
        if len(parts) != 3:
            text, ui = _build_summary_payload("❌ 菜单参数无效，请重新选择。")
            await edit_callback_message(ctx, text, ui=ui, message_id=message_id)
            return
        _, role, raw_index = parts
        normalized_role = normalize_model_role(role)
        try:
            index = int(raw_index)
        except Exception:
            index = -1

        config = reload_models_config()
        models = _resolve_cached_role_models(ctx, config, role)
        if not normalized_role or index < 0 or index >= len(models):
            text, ui = _build_summary_payload("❌ 菜单已过期，请重新选择。")
            await edit_callback_message(ctx, text, ui=ui, message_id=message_id)
            return

        model_key = models[index]
        try:
            result = update_configured_model(normalized_role, model_key)
        except FileNotFoundError:
            text, ui = _build_summary_payload(
                f"❌ 找不到模型配置文件：`{resolve_models_config_path()}`"
            )
            await edit_callback_message(ctx, text, ui=ui, message_id=message_id)
            return
        except json.JSONDecodeError as exc:
            text, ui = _build_summary_payload(f"❌ 模型配置文件不是合法 JSON：`{exc}`")
            await edit_callback_message(ctx, text, ui=ui, message_id=message_id)
            return
        except ValueError as exc:
            text, ui = _build_summary_payload(f"❌ {exc}")
            await edit_callback_message(ctx, text, ui=ui, message_id=message_id)
            return

        text, ui = _build_summary_payload(
            "✅ 已通过菜单切换模型\n\n"
            f"- 角色：`{result['role']}`\n"
            f"- 原值：`{result['previous'] or '未配置'}`\n"
            f"- 新值：`{result['current']}`"
        )
        await edit_callback_message(ctx, text, ui=ui, message_id=message_id)
        return

    text, ui = _build_summary_payload("❌ 未识别的模型菜单操作。")
    await edit_callback_message(ctx, text, ui=ui, message_id=message_id)
