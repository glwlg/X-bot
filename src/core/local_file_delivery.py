from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from core.file_artifacts import classify_file_kind
from core.platform.models import UnifiedContext

logger = logging.getLogger(__name__)

_DEFAULT_MAX_FILE_BYTES = max(
    1,
    int(os.getenv("LOCAL_FILE_DELIVERY_MAX_FILE_MB", "49")) * 1024 * 1024,
)
_SUPPORTED_KINDS = {"auto", "document", "photo", "video", "audio"}


def _resolve_target_path(path: str, task_workspace_root: str = "") -> Path:
    raw = str(path or "").strip()
    if not raw:
        raise ValueError("path is required")

    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        base = Path(task_workspace_root or os.getcwd()).expanduser()
        candidate = base / candidate
    return candidate.resolve(strict=False)


def _sensitive_path_reason(path_obj: Path) -> str:
    basename = path_obj.name.strip().lower()
    if basename == ".env" or basename.startswith(".env."):
        return f"environment file blocked: {basename}"

    return ""


def validate_local_delivery_target(
    path: str,
    *,
    task_workspace_root: str = "",
    max_bytes: int = _DEFAULT_MAX_FILE_BYTES,
) -> tuple[Path | None, str]:
    try:
        path_obj = _resolve_target_path(path, task_workspace_root=task_workspace_root)
    except Exception as exc:
        return None, str(exc)

    sensitive_reason = _sensitive_path_reason(path_obj)
    if sensitive_reason:
        return None, sensitive_reason

    if not path_obj.exists():
        return None, f"file does not exist: {path_obj}"
    if not path_obj.is_file():
        return None, f"path is not a file: {path_obj}"
    if not os.access(path_obj, os.R_OK):
        return None, f"file is not readable by bot process: {path_obj}"

    size_bytes = int(path_obj.stat().st_size or 0)
    if size_bytes > max(1, int(max_bytes or _DEFAULT_MAX_FILE_BYTES)):
        return None, f"file is too large to send: {size_bytes} bytes"

    return path_obj, ""


def _resolve_delivery_kind(kind: str, *, platform: str, path_obj: Path) -> str:
    requested = str(kind or "auto").strip().lower() or "auto"
    if requested not in _SUPPORTED_KINDS:
        raise ValueError(f"unsupported kind: {requested}")

    resolved = classify_file_kind(path_obj.name) if requested == "auto" else requested
    safe_platform = str(platform or "").strip().lower()

    if safe_platform == "weixin" and resolved == "audio":
        return "document"
    return resolved


def _platform_supports_local_delivery(
    platform: str, delivery_kind: str
) -> tuple[bool, str]:
    safe_platform = str(platform or "").strip().lower()
    if safe_platform == "dingtalk":
        return (
            False,
            "当前钉钉通道暂不支持直接发送服务器本地文件。",
        )
    _ = delivery_kind
    return True, ""


async def _send_document(
    ctx: UnifiedContext,
    *,
    path_obj: Path,
    filename: str,
    caption: str | None,
) -> str:
    document: str | bytes = str(path_obj)
    output_name = filename
    if filename.lower().endswith(".md"):
        try:
            from services.md_converter import adapt_md_file_for_platform

            adapted_bytes, adapted_name = adapt_md_file_for_platform(
                file_bytes=path_obj.read_bytes(),
                filename=filename,
                platform=str(getattr(ctx.message, "platform", "") or ""),
            )
            document = adapted_bytes
            output_name = adapted_name
        except Exception:
            document = str(path_obj)
    await ctx.reply_document(
        document=document,
        filename=output_name,
        caption=caption,
    )
    return output_name


async def send_local_file(
    ctx: UnifiedContext,
    *,
    path: str,
    caption: str = "",
    filename: str = "",
    kind: str = "auto",
    task_workspace_root: str = "",
    max_bytes: int = _DEFAULT_MAX_FILE_BYTES,
) -> dict[str, Any]:
    path_obj, validation_error = validate_local_delivery_target(
        path,
        task_workspace_root=task_workspace_root,
        max_bytes=max_bytes,
    )
    if path_obj is None:
        lowered = str(validation_error or "").lower()
        terminal = any(
            token in lowered
            for token in (
                "environment file blocked",
                "too large",
                "not a file",
                "not readable",
            )
        )
        failure_mode = "fatal" if terminal else "recoverable"
        return {
            "ok": False,
            "error_code": "invalid_delivery_target",
            "message": validation_error or "invalid delivery target",
            "text": f"❌ 无法发送文件：{validation_error or '路径无效'}",
            "failure_mode": failure_mode,
            "terminal": terminal,
        }

    safe_platform = str(getattr(ctx.message, "platform", "") or "").strip().lower()
    try:
        delivery_kind = _resolve_delivery_kind(
            kind,
            platform=safe_platform,
            path_obj=path_obj,
        )
    except Exception as exc:
        return {
            "ok": False,
            "error_code": "invalid_args",
            "message": str(exc),
            "text": f"❌ 文件发送参数无效：{exc}",
            "failure_mode": "recoverable",
        }

    supported, support_reason = _platform_supports_local_delivery(
        safe_platform,
        delivery_kind,
    )
    if not supported:
        return {
            "ok": False,
            "error_code": "platform_unsupported",
            "message": support_reason,
            "text": f"❌ {support_reason}",
            "failure_mode": "fatal",
            "terminal": True,
        }

    output_name = Path(str(filename or "").strip()).name or path_obj.name
    safe_caption = str(caption or "").strip()[:500] or None

    try:
        if delivery_kind == "photo":
            await ctx.reply_photo(str(path_obj), caption=safe_caption)
            delivered_name = output_name
        elif delivery_kind == "video":
            await ctx.reply_video(str(path_obj), caption=safe_caption)
            delivered_name = output_name
        elif delivery_kind == "audio":
            await ctx.reply_audio(str(path_obj), caption=safe_caption)
            delivered_name = output_name
        else:
            delivered_name = await _send_document(
                ctx,
                path_obj=path_obj,
                filename=output_name,
                caption=safe_caption,
            )
    except Exception as exc:
        return {
            "ok": False,
            "error_code": "send_failed",
            "message": str(exc),
            "text": f"❌ 发送文件失败：{exc}",
            "failure_mode": "recoverable",
        }

    summary_text = f"📎 已发送文件：{delivered_name}"
    file_payload = {
        "path": str(path_obj),
        "filename": delivered_name,
        "kind": delivery_kind,
        "caption": safe_caption or "",
    }
    try:
        platform_name = str(getattr(getattr(ctx, "message", None), "platform", "") or "")
        if platform_name.strip().lower() == "heartbeat_daemon":
            pending_rows = ctx.user_data.get("heartbeat_pending_files")
            if not isinstance(pending_rows, list):
                pending_rows = []
            pending_rows.append(dict(file_payload))
            ctx.user_data["heartbeat_pending_files"] = pending_rows
            logger.info(
                "Buffered heartbeat file delivery user=%s path=%s kind=%s filename=%s total=%s",
                str(getattr(getattr(getattr(ctx, "message", None), "user", None), "id", "") or ""),
                str(path_obj),
                delivery_kind,
                delivered_name,
                len(pending_rows),
            )
    except Exception:
        pass
    return {
        "ok": True,
        "terminal": False,
        "summary": f"Sent local file {delivered_name}",
        "text": summary_text,
        "payload": {
            "text": summary_text,
            "files": [file_payload],
        },
        "files": [file_payload],
        "data": {
            "path": str(path_obj),
            "filename": delivered_name,
            "kind": delivery_kind,
            "size_bytes": int(path_obj.stat().st_size or 0),
            "platform": safe_platform,
        },
    }
