from __future__ import annotations

import asyncio
import base64
import logging
import os
from contextlib import suppress
from pathlib import Path
from typing import Any

from services.ai_service import AiService
from shared.contracts.dispatch import TaskEnvelope, TaskResult

logger = logging.getLogger(__name__)

_ai_service = AiService()
_VALID_RECORD_TYPES = {"支出", "收入", "转账"}


def _build_accounting_draft_tool_declaration() -> dict:
    return {
        "name": "submit_accounting_draft",
        "description": (
            "提交这张交易图片的结构化记账草稿。"
            "必须基于图片内容提取字段后调用此工具。"
        ).strip(),
        "parameters": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": sorted(_VALID_RECORD_TYPES),
                },
                "amount": {"type": "number"},
                "category": {"type": "string"},
                "account": {"type": "string"},
                "target_account": {"type": "string"},
                "payee": {"type": "string"},
                "remark": {"type": "string"},
                "record_time": {"type": "string"},
            },
            "required": ["type", "amount", "category", "account"],
        },
    }


def _build_system_instruction() -> str:
    return (
        "你是网页图片自动记账执行器。\n"
        "目标：先识别交易图片中的关键信息，再调用唯一可用的工具 `submit_accounting_draft` 提交结构化记账草稿。\n"
        "限制：禁止调用 `load_skill`、`skill_manager`、`read`、`write`、`edit`、`bash`，"
        "也禁止调用任何未出现在工具列表中的工具。\n"
        "要求：优先提取 `type`、`amount`、`category`、`account`、`target_account`、"
        "`payee`、`remark`、`record_time`。\n"
        "当字段不完整时，可做保守推断，但不要编造明显不存在的信息。\n"
        "一旦 `submit_accounting_draft` 调用成功，立即停止，不要继续尝试其他工具。"
    ).strip()


def _normalize_accounting_draft(
    data: dict[str, object],
    *,
    fallback_book_id: Any,
) -> dict[str, Any]:
    record_type = str(data.get("type") or "").strip()
    if record_type not in _VALID_RECORD_TYPES:
        raise ValueError(f"交易类型不支持：{record_type or '空'}")

    try:
        amount = float(data.get("amount") or 0)
    except (TypeError, ValueError):
        raise ValueError(f"金额解析错误：{data.get('amount')}")
    if amount <= 0:
        raise ValueError("金额必须大于 0")

    category_name = str(data.get("category") or "").strip() or "未分类"
    account_name = str(data.get("account") or "").strip()
    if not account_name:
        raise ValueError("账户不能为空，请补充支付账户")

    target_account_name = str(data.get("target_account") or "").strip()
    if record_type == "转账" and not target_account_name:
        raise ValueError("转账必须填写 target_account")

    try:
        book_id = int(fallback_book_id) if fallback_book_id is not None else 0
    except (TypeError, ValueError):
        book_id = 0

    return {
        "type": record_type,
        "amount": amount,
        "category_name": category_name,
        "account_name": account_name,
        "target_account_name": target_account_name,
        "payee": str(data.get("payee") or "").strip()[:100],
        "remark": str(data.get("remark") or "").strip()[:500],
        "record_time": str(data.get("record_time") or "").strip(),
        "book_id": book_id,
    }


def _load_image(task: TaskEnvelope) -> tuple[bytes, str]:
    metadata = dict(task.metadata or {})
    path_text = str(metadata.get("web_accounting_image_path") or "").strip()
    if not path_text:
        raise RuntimeError("missing web_accounting_image_path")

    data_dir = str(os.getenv("DATA_DIR", "/app/data")).strip() or "/app/data"
    allow_root = (
        Path(data_dir).expanduser().resolve() / "system" / "web_accounting_uploads"
    )
    image_path = Path(path_text).expanduser().resolve()
    if not str(image_path).startswith(str(allow_root)):
        raise RuntimeError("invalid image path")
    if not image_path.exists() or not image_path.is_file():
        raise RuntimeError("uploaded image not found")

    raw = image_path.read_bytes()
    if not raw:
        raise RuntimeError("uploaded image is empty")
    if len(raw) > 8 * 1024 * 1024:
        raise RuntimeError("uploaded image too large")

    mime_type = str(metadata.get("web_accounting_image_mime") or "").strip().lower()
    if not mime_type.startswith("image/"):
        mime_type = "image/jpeg"
    return raw, mime_type


def _extract_accounting_draft(data: dict[str, Any]) -> dict[str, Any]:
    payload = data.get("payload") if isinstance(data, dict) else None
    if isinstance(payload, dict):
        draft = payload.get("draft") or payload.get("accounting_draft")
        if isinstance(draft, dict):
            return dict(draft)
    draft = data.get("draft") if isinstance(data, dict) else None
    if isinstance(draft, dict):
        return dict(draft)
    return {}


async def run_web_accounting_auto_image_task(task: TaskEnvelope) -> TaskResult:
    metadata = dict(task.metadata or {})

    image_bytes, mime_type = _load_image(task)
    tool_decl = _build_accounting_draft_tool_declaration()
    system_instruction = _build_system_instruction()

    called = 0
    last_error = ""
    final_text = ""
    captured_data: dict[str, Any] = {}
    stream_timed_out = False
    stream_error = ""
    stream_timeout_sec = max(
        20.0,
        float(os.getenv("WEB_ACCOUNTING_STREAM_TIMEOUT_SEC", "75")),
    )
    loop = asyncio.get_running_loop()
    success_future: asyncio.Future[dict[str, Any]] = loop.create_future()

    async def tool_executor(name: str, args: dict[str, object]) -> dict:
        nonlocal called, last_error, captured_data
        if name != "submit_accounting_draft":
            last_error = f"Tool not allowed: {name}"
            return {
                "ok": False,
                "error_code": "tool_not_allowed",
                "message": f"Tool not allowed: {name}",
                "failure_mode": "recoverable",
            }
        called += 1
        try:
            draft = _normalize_accounting_draft(
                dict(args or {}),
                fallback_book_id=metadata.get("accounting_book_id"),
            )
        except ValueError as exc:
            last_error = str(exc or "记账草稿校验失败").strip()
            return {
                "ok": False,
                "error_code": "invalid_accounting_draft",
                "message": last_error,
                "failure_mode": "recoverable",
            }

        tool_result = {
            "ok": True,
            "terminal": True,
            "task_outcome": "done",
            "payload": {
                "text": "已提取记账草稿",
                "message": "已提取记账草稿",
                "draft": draft,
                "book_id": int(draft.get("book_id") or 0),
            }
        }

        payload = tool_result.get("payload")
        captured_data = dict(tool_result)
        if isinstance(payload, dict):
            captured_data["payload"] = dict(payload)

        extracted_draft = _extract_accounting_draft(captured_data)
        if extracted_draft and not success_future.done():
            success_future.set_result(
                {
                    "draft": extracted_draft,
                    "book_id": int(extracted_draft.get("book_id") or 0),
                }
            )
        else:
            failure_text = str(
                (
                    payload.get("text")
                    if isinstance(payload, dict)
                    else ""
                )
                or tool_result.get("message")
                or tool_result.get("text")
                or tool_result.get("summary")
                or ""
            ).strip()
            last_error = (
                failure_text[:500]
                if failure_text
                else "submit_accounting_draft 未返回有效的 draft"
            )
        return tool_result

    message_history = [
        {
            "role": "user",
            "parts": [
                {"text": str(task.instruction or "")},
                {
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": base64.b64encode(image_bytes).decode("utf-8"),
                    }
                },
            ],
        }
    ]

    async def consume_stream() -> None:
        nonlocal final_text
        async for chunk in _ai_service.generate_response_stream(
            message_history,
            tools=[tool_decl],
            tool_executor=tool_executor,
            system_instruction=system_instruction,
        ):
            final_text += str(chunk or "")

    consume_task = asyncio.create_task(consume_stream())
    try:
        done, _ = await asyncio.wait(
            {consume_task, success_future},
            return_when=asyncio.FIRST_COMPLETED,
            timeout=stream_timeout_sec,
        )

        if success_future in done:
            result = success_future.result()
            if not consume_task.done():
                consume_task.cancel()
                with suppress(asyncio.CancelledError):
                    await consume_task
            success_text = "草稿解析成功"
            return TaskResult(
                task_id=task.task_id,
                executor_id=task.executor_id,
                ok=True,
                summary=success_text,
                payload={
                    "text": success_text,
                    "message": success_text,
                    "draft": dict(result.get("draft") or {}),
                    "book_id": int(result.get("book_id") or 0),
                    "tool_called": called,
                },
            )

        if consume_task in done:
            try:
                await consume_task
            except Exception as exc:
                stream_error = str(exc or "").strip()
        else:
            stream_timed_out = True
            if not consume_task.done():
                consume_task.cancel()
                with suppress(asyncio.CancelledError):
                    await consume_task
    finally:
        if not consume_task.done():
            consume_task.cancel()
            with suppress(asyncio.CancelledError):
                await consume_task

    draft = _extract_accounting_draft(captured_data)
    if draft:
        success_text = "草稿解析成功"
        return TaskResult(
            task_id=task.task_id,
            executor_id=task.executor_id,
            ok=True,
            summary=success_text,
            payload={
                "text": success_text,
                "message": success_text,
                "draft": draft,
                "book_id": int(draft.get("book_id") or 0),
                "tool_called": called,
            },
        )

    detail = (
        last_error
        or stream_error
        or str(final_text or "").strip()
        or ("AI 识别超时，请稍后重试。" if stream_timed_out else "")
        or "AI 未能完成记账，请补充信息后重试。"
    )
    logger.warning(
        "web accounting auto-image failed task_id=%s detail=%s",
        task.task_id,
        detail[:200],
    )
    return TaskResult(
        task_id=task.task_id,
        executor_id=task.executor_id,
        ok=False,
        summary=detail[:200],
        error=detail[:200],
        payload={
            "text": detail[:500],
            "message": detail[:500],
            "draft": draft,
            "book_id": int(metadata.get("accounting_book_id") or 0),
            "tool_called": called,
        },
    )
