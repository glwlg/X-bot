from __future__ import annotations

import argparse
import inspect
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from core.platform.models import Chat, MessageType, UnifiedContext, UnifiedMessage, User

TOOL_RESULT_PREFIX = "tool_result="


def prepare_default_env(repo_root: Path) -> None:
    data_dir = (repo_root / "data").resolve()
    raw_models_config_path = str(
        os.getenv("MODELS_CONFIG_PATH", "config/models.json") or "config/models.json"
    ).strip()
    models_config_path = Path(raw_models_config_path)
    if not models_config_path.is_absolute():
        models_config_path = (repo_root / models_config_path).resolve()
    os.environ.setdefault("DATA_DIR", str(data_dir))
    os.environ["MODELS_CONFIG_PATH"] = str(models_config_path)
    os.environ.setdefault(
        "MANAGER_DISPATCH_ROOT",
        str((data_dir / "system" / "dispatch").resolve()),
    )
    os.environ.setdefault(
        "X_DEPLOYMENT_STAGING_PATH",
        str((data_dir / "system" / "deployment_staging").resolve()),
    )


def parse_json_object(raw: str, *, option_name: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        loaded = json.loads(text)
    except Exception as exc:
        raise SystemExit(f"invalid {option_name} JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise SystemExit(f"{option_name} must be a JSON object")
    return loaded


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--user-id",
        default=os.getenv("X_BOT_RUNTIME_USER_ID", "1"),
        help="Runtime user id. Defaults to X_BOT_RUNTIME_USER_ID or 1.",
    )
    parser.add_argument(
        "--platform",
        default=os.getenv("X_BOT_RUNTIME_PLATFORM", "telegram"),
        help="Runtime platform. Defaults to X_BOT_RUNTIME_PLATFORM or telegram.",
    )
    parser.add_argument(
        "--chat-id",
        default=os.getenv("X_BOT_RUNTIME_CHAT_ID", ""),
        help="Chat id for the synthetic runtime context. Defaults to X_BOT_RUNTIME_CHAT_ID or user id.",
    )
    parser.add_argument(
        "--message-text",
        default="",
        help="Optional synthetic message text injected into ctx.message.text.",
    )
    parser.add_argument(
        "--user-data",
        default="{}",
        help="Optional JSON object injected into ctx.user_data.",
    )
    parser.add_argument(
        "--params-json",
        default="",
        help="Optional JSON object merged into params before execution.",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Directory for files emitted by the skill. Defaults to DATA_DIR/user/skills/<skill>/outputs.",
    )
    parser.add_argument(
        "--raw-json",
        action="store_true",
        help="Print machine-readable tool_result JSON instead of the default rendered view.",
    )


def build_context_from_args(args: argparse.Namespace) -> UnifiedContext:
    user_id = str(getattr(args, "user_id", "") or "1").strip() or "1"
    platform = str(getattr(args, "platform", "") or "telegram").strip() or "telegram"
    chat_id = str(getattr(args, "chat_id", "") or user_id).strip() or user_id
    message_text = str(getattr(args, "message_text", "") or "")

    message = UnifiedMessage(
        id="cli-message",
        platform=platform,
        user=User(id=user_id),
        chat=Chat(id=chat_id, type="private"),
        date=datetime.now().astimezone(),
        type=MessageType.TEXT,
        text=message_text,
    )
    ctx = UnifiedContext(
        message=message,
        platform_ctx=None,
        platform_event=None,
        _adapter=None,
        user=message.user,
    )
    ctx._ephemeral_user_data = parse_json_object(
        str(getattr(args, "user_data", "{}") or "{}"),
        option_name="--user-data",
    )
    return ctx


def merge_params(
    args: argparse.Namespace,
    explicit_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged = parse_json_object(
        str(getattr(args, "params_json", "") or ""),
        option_name="--params-json",
    )
    for key, value in (explicit_params or {}).items():
        if value is not None:
            merged[key] = value
    return merged


def parse_csv_values(raw: str | None) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


async def run_execute_cli(
    execute_fn: Any,
    *,
    args: argparse.Namespace,
    params: dict[str, Any],
) -> int:
    ctx = build_context_from_args(args)
    result = execute_fn(ctx, params, runtime=None)
    rendered = await _collect_result(result)
    output_dir = _resolve_output_dir(args, execute_fn=execute_fn)
    if bool(getattr(args, "raw_json", False)):
        normalized = _normalize_raw_tool_result(rendered)
        print(
            f"{TOOL_RESULT_PREFIX}"
            + json.dumps(normalized, ensure_ascii=False, default=_json_default)
        )
        return _exit_code_from_item(normalized)
    return _render_default(rendered, output_dir=output_dir)


async def _collect_result(result: Any) -> Any:
    if inspect.isasyncgen(result):
        chunks = []
        async for item in result:
            chunks.append(item)
        return chunks
    if inspect.isawaitable(result):
        return await result
    return result


def _render_default(rendered: Any, *, output_dir: str) -> int:
    if isinstance(rendered, list):
        exit_code = 0
        for item in rendered:
            exit_code = max(exit_code, _render_default(item, output_dir=output_dir))
        return exit_code

    if rendered is None:
        return 0

    if isinstance(rendered, dict):
        text = rendered.get("text")
        if text:
            print(str(text))
        saved_paths = _save_files(rendered.get("files"), output_dir=output_dir)
        for path in saved_paths:
            print(f"saved_file={path}")
        if not text and not saved_paths:
            print(json.dumps(rendered, ensure_ascii=False, indent=2, default=_json_default))
        return _exit_code_from_item(rendered)

    print(str(rendered))
    return 0


def _save_files(files: Any, *, output_dir: str) -> list[str]:
    if not isinstance(files, dict) or not files:
        return []
    root = Path(output_dir or ".").resolve()
    root.mkdir(parents=True, exist_ok=True)

    saved_paths: list[str] = []
    for raw_name, payload in files.items():
        target = (root / str(raw_name)).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, (bytes, bytearray)):
            target.write_bytes(bytes(payload))
        else:
            target.write_text(str(payload), encoding="utf-8")
        saved_paths.append(str(target))
    return saved_paths


def _resolve_output_dir(args: argparse.Namespace, *, execute_fn: Any) -> str:
    explicit = str(getattr(args, "output_dir", "") or "").strip()
    if explicit:
        return explicit

    configured = str(os.getenv("X_BOT_SKILL_OUTPUT_DIR", "") or "").strip()
    if configured:
        return configured

    data_dir = Path(str(os.getenv("DATA_DIR", "data") or "data")).expanduser()
    if not data_dir.is_absolute():
        data_dir = data_dir.resolve()

    skill_name = _infer_skill_name(execute_fn)
    return str((data_dir / "user" / "skills" / skill_name / "outputs").resolve())


def _infer_skill_name(execute_fn: Any) -> str:
    code_obj = getattr(execute_fn, "__code__", None)
    filename = str(getattr(code_obj, "co_filename", "") or "").strip()
    if filename:
        path = Path(filename).resolve()
        parts = list(path.parts)
        for root_name in ("builtin", "learned"):
            if root_name in parts:
                idx = parts.index(root_name)
                if idx + 1 < len(parts):
                    candidate = str(parts[idx + 1] or "").strip()
                    if candidate:
                        return candidate
    module_name = str(getattr(execute_fn, "__module__", "") or "").strip()
    if module_name:
        parts = [part for part in module_name.split(".") if part]
        if "scripts" in parts:
            idx = parts.index("scripts")
            if idx - 1 >= 0:
                return parts[idx - 1]
        return parts[-1]
    return "unknown_skill"


def _exit_code_from_rendered(rendered: Any) -> int:
    if isinstance(rendered, list):
        return max((_exit_code_from_rendered(item) for item in rendered), default=0)
    return _exit_code_from_item(rendered)


def _exit_code_from_item(item: Any) -> int:
    if not isinstance(item, dict):
        return 0
    if item.get("ok") is False:
        return 1
    if item.get("success") is False:
        return 1
    if str(item.get("task_outcome") or "").strip().lower() == "failed":
        return 1
    if str(item.get("failure_mode") or "").strip().lower() == "fatal":
        return 1
    return 0


def _normalize_raw_tool_result(rendered: Any) -> dict[str, Any]:
    if isinstance(rendered, dict):
        return dict(rendered)

    if isinstance(rendered, list):
        final_dict: dict[str, Any] | None = None
        progress_messages: list[str] = []
        for item in rendered:
            if isinstance(item, dict):
                final_dict = dict(item)
                continue
            text = str(item or "").strip()
            if text:
                progress_messages.append(text)

        if final_dict is None:
            if progress_messages:
                return {"ok": True, "text": progress_messages[-1]}
            return {"ok": True}

        if progress_messages and "progress_messages" not in final_dict:
            final_dict["progress_messages"] = progress_messages[-20:]
        return final_dict

    if rendered is None:
        return {"ok": True}

    text = str(rendered or "").strip()
    if not text:
        return {"ok": True}
    return {"ok": True, "text": text}


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return f"<binary:{len(value)} bytes>"
    return str(value)
