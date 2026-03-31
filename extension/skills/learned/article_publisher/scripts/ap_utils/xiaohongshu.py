"""Xiaohongshu (RED) publishing logic via opencli."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ap_utils import (
    MAX_SOCIAL_CONTEXT_CHARS,
    article_plain_text,
    html_to_plain_text,
    parse_article_json,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Note generation
# ---------------------------------------------------------------------------

def normalize_xiaohongshu_tags(raw_tags: Any, topic: str) -> list[str]:
    tokens: list[str] = []
    if isinstance(raw_tags, str):
        tokens.extend(re.split(r"[#\s,，]+", raw_tags))
    elif isinstance(raw_tags, list):
        for item in raw_tags:
            tokens.extend(re.split(r"[#\s,，]+", str(item or "")))

    normalized: list[str] = []
    for token in tokens:
        cleaned = str(token or "").strip().lstrip("#")
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned[:24])
    if not normalized and str(topic or "").strip():
        normalized.append(str(topic).strip()[:24])
    return normalized[:8]


def fallback_xiaohongshu_note(topic: str, article_data: dict[str, Any]) -> dict[str, Any]:
    title = str(article_data.get("title") or topic or "小红书笔记").strip()
    title = title[:20].rstrip()
    body = article_plain_text(article_data)
    if not body:
        body = str(article_data.get("digest") or topic or "").strip()
    return {
        "title": title or "小红书笔记",
        "body": body,
        "tags": normalize_xiaohongshu_tags([], topic or title),
    }


def normalize_xiaohongshu_note_data(
    data: dict[str, Any],
    *,
    topic: str,
    article_data: dict[str, Any],
) -> dict[str, Any]:
    fallback = fallback_xiaohongshu_note(topic, article_data)
    title = str(data.get("title") or fallback["title"]).strip() or fallback["title"]
    title = title[:20].rstrip() or fallback["title"]
    body = html_to_plain_text(str(data.get("body") or fallback["body"])).strip()
    if not body:
        body = fallback["body"]
    tags = normalize_xiaohongshu_tags(data.get("tags"), topic or title)
    if not tags:
        tags = list(fallback["tags"])
    return {"title": title, "body": body, "tags": tags}


async def generate_xiaohongshu_note_json(
    topic: str,
    article_data: dict[str, Any],
) -> dict[str, Any]:
    from core.config import get_client_for_model
    from core.model_config import get_current_model
    from services.openai_adapter import generate_text

    article_plain = article_plain_text(article_data)
    note_prompt = (
        f"你是一位擅长科技内容运营的小红书编辑，请把下面的文章整理成一篇适合小红书图文发布的笔记。\n"
        f"主题：{topic}\n"
        f"文章标题：{article_data.get('title') or topic}\n"
        f"文章正文：\n{article_plain[:MAX_SOCIAL_CONTEXT_CHARS]}\n\n"
        "要求：\n"
        "1. 返回严格 JSON。\n"
        '2. JSON 格式：{"title":"...","body":"...","tags":["标签1","标签2"]}。\n'
        "3. title 控制在 20 个汉字以内，避免夸张标题党。\n"
        "4. body 使用纯文本和换行，不要 Markdown，不要 HTML。\n"
        "5. tags 返回 3 到 8 个，不带 #。\n"
        "6. 仅返回 JSON 对象，不要额外解释。"
    )

    model_to_use = get_current_model()
    if not model_to_use:
        raise RuntimeError("No text model configured in config/models.json")
    async_client = get_client_for_model(model_to_use, is_async=True)
    if async_client is None:
        raise RuntimeError("OpenAI async client is not initialized")

    response_text = await generate_text(
        async_client=async_client,
        model=model_to_use,
        contents=note_prompt,
        config={"response_mime_type": "application/json"},
    )
    payload = parse_article_json(str(response_text or ""))
    return normalize_xiaohongshu_note_data(
        payload,
        topic=topic,
        article_data=article_data,
    )


def build_xiaohongshu_note_attachment(note_data: dict[str, Any]) -> bytes:
    tags = " ".join(f"#{tag}" for tag in list(note_data.get("tags") or []) if str(tag).strip())
    lines = [
        f"标题：{str(note_data.get('title') or '').strip()}",
        "",
        "正文：",
        str(note_data.get("body") or "").strip(),
    ]
    if tags:
        lines.extend(["", "标签：", tags])
    return ("\n".join(lines).strip() + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# opencli subprocess helpers
# ---------------------------------------------------------------------------

async def _run_subprocess(
    args: list[str],
    *,
    timeout_seconds: float,
) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RuntimeError(
            f"Command timed out after {int(timeout_seconds)}s: {' '.join(args[:4])}"
        ) from None
    return (
        int(proc.returncode or 0),
        stdout.decode("utf-8", errors="ignore"),
        stderr.decode("utf-8", errors="ignore"),
    )


def _condense_command_output(text: str, *, limit: int = 300) -> str:
    condensed = re.sub(r"\s+", " ", str(text or "").strip())
    if len(condensed) <= limit:
        return condensed
    return condensed[: limit - 3].rstrip() + "..."


def _resolve_opencli_path() -> str:
    configured = str(os.getenv("OPENCLI_BIN") or "").strip()
    if configured:
        return configured
    return shutil.which("opencli") or ""


def _sanitize_opencli_publish_cmd(cmd: list[str]) -> str:
    sanitized: list[str] = []
    idx = 0
    while idx < len(cmd):
        token = str(cmd[idx] or "")
        if idx == 3:
            sanitized.append(f"<content:{len(token)} chars>")
        elif token == "--images" and idx + 1 < len(cmd):
            image_names = [
                Path(item).name
                for item in str(cmd[idx + 1] or "").split(",")
                if str(item or "").strip()
            ]
            sanitized.extend([token, ",".join(image_names)])
            idx += 1
        else:
            sanitized.append(token)
        idx += 1
    return " ".join(sanitized)


def _load_json_from_mixed_text(text: str) -> Any | None:
    raw = str(text or "").strip()
    if not raw:
        return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    for candidate in reversed(lines):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    decoder = json.JSONDecoder()
    for idx_char, char in enumerate(raw):
        if char not in "[{":
            continue
        try:
            payload, _end = decoder.raw_decode(raw[idx_char:])
        except json.JSONDecodeError:
            continue
        return payload
    return None


def _format_opencli_publish_error(
    *,
    code: int | None = None,
    status: str = "",
    detail: str = "",
    stdout: str = "",
    stderr: str = "",
) -> str:
    parts: list[str] = []
    if status:
        parts.append(f"status={status}")
    if detail:
        parts.append(detail)
    if code not in (None, 0):
        parts.append(f"exit={code}")

    stdout_summary = _condense_command_output(stdout)
    stderr_summary = _condense_command_output(stderr)
    if stderr_summary:
        parts.append(f"stderr={stderr_summary}")
    if stdout_summary and stdout_summary not in parts and stdout_summary != detail:
        parts.append(f"stdout={stdout_summary}")

    return "; ".join(part for part in parts if part) or "unknown opencli publish error"


def _opencli_doctor_connected(output_text: str) -> bool:
    normalized = str(output_text or "").strip().lower()
    return (
        "[ok] extension: connected" in normalized
        and "[ok] connectivity: connected" in normalized
    )


def _opencli_has_xiaohongshu_session(output_text: str) -> bool:
    normalized = str(output_text or "").strip().lower()
    return "site:xiaohongshu" in normalized


def _format_opencli_command_error(
    *,
    action: str,
    code: int | None = None,
    stdout: str = "",
    stderr: str = "",
    detail: str = "",
) -> str:
    parts: list[str] = [action]
    if detail:
        parts.append(detail)
    if code not in (None, 0):
        parts.append(f"exit={code}")
    stderr_summary = _condense_command_output(stderr)
    stdout_summary = _condense_command_output(stdout)
    if stderr_summary:
        parts.append(f"stderr={stderr_summary}")
    elif stdout_summary:
        parts.append(f"stdout={stdout_summary}")
    return "; ".join(part for part in parts if part)


async def _ensure_xiaohongshu_opencli_session(
    opencli_path: str,
    *,
    phase: str,
    force_warmup: bool,
) -> str:
    try:
        doctor_code, doctor_stdout, doctor_stderr = await _run_subprocess(
            [opencli_path, "doctor", "--sessions"],
            timeout_seconds=20.0,
        )
    except Exception as exc:
        return f"⚠️ 发布中止：{phase}无法执行 `opencli doctor --sessions`：{exc}"

    doctor_text = "\n".join(
        part for part in (doctor_stdout, doctor_stderr) if str(part or "").strip()
    ).strip()
    doctor_summary = _condense_command_output(doctor_text) or "unknown doctor output"

    if doctor_code != 0:
        return (
            f"⚠️ 发布中止：{phase}检测到 opencli 会话异常："
            + _format_opencli_command_error(
                action="doctor --sessions",
                code=doctor_code,
                stdout=doctor_stdout,
                stderr=doctor_stderr,
            )
        )

    if not _opencli_doctor_connected(doctor_text):
        return (
            f"⚠️ 发布中止：{phase}检测到 opencli bridge 未连接：{doctor_summary}"
        )

    if not force_warmup and _opencli_has_xiaohongshu_session(doctor_text):
        return ""

    warmup_errors: list[str] = []
    for attempt in range(2):
        cmd = [opencli_path, "xiaohongshu", "creator-profile", "-f", "json"]
        logger.info(
            "opencli xiaohongshu warmup attempt %s starting (%s): %s",
            attempt + 1,
            phase,
            " ".join(cmd),
        )
        code, stdout, stderr = await _run_subprocess(cmd, timeout_seconds=90.0)
        logger.info(
            "opencli xiaohongshu warmup attempt %s finished (%s): code=%s stdout=%s stderr=%s",
            attempt + 1,
            phase,
            code,
            _condense_command_output(stdout) or "<empty>",
            _condense_command_output(stderr) or "<empty>",
        )
        if code == 0 and str(stdout or "").strip():
            return ""

        warmup_errors.append(
            _format_opencli_command_error(
                action=f"creator-profile warmup {attempt + 1}",
                code=code,
                stdout=stdout,
                stderr=stderr,
            )
        )
        if attempt == 0:
            await asyncio.sleep(2)

    return (
        f"⚠️ 发布中止：{phase}小红书会话预热失败："
        + "；".join(warmup_errors)
        + f"；doctor={doctor_summary}"
    )


async def prepare_xiaohongshu_opencli() -> str:
    opencli_path = _resolve_opencli_path()
    if not opencli_path:
        return "⚠️ 发布中止：未找到 `opencli` 命令，请先安装并确保它在 PATH 中。"
    try:
        code, _, stderr = await _run_subprocess(
            [opencli_path, "xiaohongshu", "publish", "--help"],
            timeout_seconds=15.0,
        )
    except Exception as exc:
        return f"⚠️ 发布中止：无法执行 `opencli xiaohongshu publish --help`：{exc}"
    if code != 0:
        detail = _condense_command_output(stderr) or "未知错误"
        return f"⚠️ 发布中止：`opencli` 小红书发布子命令不可用：{detail}"
    return await _ensure_xiaohongshu_opencli_session(
        opencli_path,
        phase="预检阶段",
        force_warmup=False,
    )


def _extract_opencli_result_row(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        if any(
            key in payload
            for key in ("status", "detail", "message", "note_id", "draft_id", "url")
        ):
            return payload
        for key in ("data", "result", "rows", "items"):
            nested = _extract_opencli_result_row(payload.get(key))
            if nested:
                return nested
    elif isinstance(payload, list):
        for item in payload:
            nested = _extract_opencli_result_row(item)
            if nested:
                return nested
    return {}


def _parse_opencli_publish_output(stdout_text: str) -> dict[str, Any]:
    text = str(stdout_text or "").strip()
    if not text:
        return {}
    payload = _load_json_from_mixed_text(text)
    if payload is None:
        return {"detail": text}
    return _extract_opencli_result_row(payload) or {"detail": text}


async def publish_to_xiaohongshu(
    *,
    topic: str,
    note_data: dict[str, Any],
    cover_bytes: bytes | None,
    section_images: dict[int, bytes],
) -> str:
    opencli_path = _resolve_opencli_path()
    if not opencli_path:
        return "❌ 小红书发布中止：未找到 `opencli` 命令。"

    session_error = await _ensure_xiaohongshu_opencli_session(
        opencli_path,
        phase="发布前",
        force_warmup=True,
    )
    if session_error:
        raise RuntimeError(session_error.removeprefix("⚠️ 发布中止：").strip())

    publish_images: list[tuple[str, bytes]] = []
    if cover_bytes:
        publish_images.append(("cover.png", cover_bytes))
    for idx, image_bytes in sorted(section_images.items()):
        if image_bytes:
            publish_images.append((f"section-{idx}.png", image_bytes))
    publish_images = publish_images[:9]
    if not publish_images:
        return "❌ 小红书发布中止：至少需要 1 张配图。"

    note_body = str(note_data.get("body") or "").strip()
    title = str(note_data.get("title") or "").strip()[:20].rstrip()
    topics = [
        str(tag or "").strip().lstrip("#")
        for tag in list(note_data.get("tags") or [])
        if str(tag or "").strip()
    ]
    if not topics and str(topic or "").strip():
        topics = [str(topic).strip()[:24]]

    async def _run_opencli_publish_once(cmd: list[str], *, attempt: int) -> str:
        safe_cmd = _sanitize_opencli_publish_cmd(cmd)
        logger.info(
            "opencli xiaohongshu publish attempt %s starting: %s",
            attempt,
            safe_cmd,
        )
        code, stdout, stderr = await _run_subprocess(cmd, timeout_seconds=300.0)
        logger.info(
            "opencli xiaohongshu publish attempt %s finished: code=%s stdout=%s stderr=%s",
            attempt,
            code,
            _condense_command_output(stdout) or "<empty>",
            _condense_command_output(stderr) or "<empty>",
        )
        if code != 0:
            raise RuntimeError(
                _format_opencli_publish_error(
                    code=code,
                    stdout=stdout,
                    stderr=stderr,
                )
            )

        result = _parse_opencli_publish_output(stdout)
        status = str(result.get("status") or "").strip().lower()
        detail = _condense_command_output(
            str(result.get("detail") or result.get("message") or stdout)
        )
        if status in {"error", "failed", "failure"}:
            raise RuntimeError(
                _format_opencli_publish_error(
                    status=status,
                    detail=detail,
                    stdout=stdout,
                    stderr=stderr,
                )
            )
        return detail

    with tempfile.TemporaryDirectory(prefix="ikaros-xhs-") as temp_dir:
        image_paths: list[str] = []
        for filename, image_bytes in publish_images:
            target_path = Path(temp_dir) / filename
            target_path.write_bytes(image_bytes)
            image_paths.append(str(target_path))

        cmd = [
            opencli_path,
            "xiaohongshu",
            "publish",
            note_body,
            "--format",
            "json",
        ]
        if title:
            cmd.extend(["--title", title])
        if image_paths:
            cmd.extend(["--images", ",".join(image_paths)])
        if topics:
            cmd.extend(["--topics", ",".join(topics[:8])])

        detail = ""
        retried = False
        attempt_errors: list[str] = []
        for attempt in range(2):
            attempt_cmd = list(cmd)
            if attempt == 1:
                attempt_cmd.append("--verbose")
            try:
                detail = await _run_opencli_publish_once(attempt_cmd, attempt=attempt + 1)
                break
            except Exception as exc:
                error_text = str(exc).strip() or "unknown error"
                attempt_errors.append(f"第{attempt + 1}次: {error_text}")
                if attempt == 0:
                    retried = True
                    logger.warning(
                        "opencli xiaohongshu publish failed on first attempt, retrying in 2s: %s",
                        error_text,
                    )
                    await asyncio.sleep(2)
                    continue
                logger.error(
                    "opencli xiaohongshu publish failed after retry: %s",
                    " | ".join(attempt_errors),
                )
                raise RuntimeError("；".join(attempt_errors))
        if not detail and attempt_errors:
            raise RuntimeError("；".join(attempt_errors))

    parts = ["✅ 已通过 opencli 提交到小红书"]
    if retried:
        parts.append("重试后成功")
    if detail:
        parts.append(detail)
    return "，".join(parts)
