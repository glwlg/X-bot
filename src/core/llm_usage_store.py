from __future__ import annotations

import contextvars
import inspect
import logging
import math
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any

from core.config import DATA_DIR
from core.model_config import get_model_id_for_api, get_models_config, load_models_config

logger = logging.getLogger(__name__)

_USAGE_TABLE = "llm_usage_daily_session_model"
_USAGE_SESSION_VAR: contextvars.ContextVar[str] = contextvars.ContextVar(
    "llm_usage_session_id",
    default="",
)

_TRACKED_METHOD_PATHS: dict[tuple[str, ...], str] = {
    ("chat", "completions", "create"): "chat.completions.create",
    ("responses", "create"): "responses.create",
    ("embeddings", "create"): "embeddings.create",
    ("audio", "transcriptions", "create"): "audio.transcriptions.create",
    ("audio", "speech", "create"): "audio.speech.create",
    ("images", "generate"): "images.generate",
    ("images", "edit"): "images.edit",
    ("images", "variations"): "images.variations",
}

_WRAP_EXCLUDED_TYPES = (
    str,
    bytes,
    bytearray,
    int,
    float,
    bool,
    type(None),
    dict,
    list,
    tuple,
    set,
    Path,
)

_ESTIMATE_SKIP_KEYS = {
    "model",
    "role",
    "type",
    "id",
    "object",
    "created",
    "usage",
    "audio",
    "file",
    "files",
    "image",
    "image_url",
    "inline_data",
    "url",
    "b64_json",
}

_ESTIMATE_SKIP_VALUE_TYPES = (
    bytes,
    bytearray,
    memoryview,
)

_BASE64_TOKEN_RE = re.compile(r"^[A-Za-z0-9+/=\s]+$")
_SESSION_TOKEN_RE = re.compile(r"[^a-zA-Z0-9_\-:.]+")


def _now_local() -> datetime:
    return datetime.now().astimezone()


def _now_iso() -> str:
    return _now_local().isoformat(timespec="seconds")


def _today_iso() -> str:
    return _now_local().date().isoformat()


def _safe_text(value: Any, *, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _int_value(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except Exception:
        return 0


def _normalize_session_id(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    safe = _SESSION_TOKEN_RE.sub("_", raw)
    return safe or ""


def current_llm_usage_session_id() -> str:
    session_id = _normalize_session_id(_USAGE_SESSION_VAR.get())
    return session_id or "system"


def set_current_llm_usage_session_id(session_id: Any) -> str:
    normalized = _normalize_session_id(session_id)
    if normalized:
        _USAGE_SESSION_VAR.set(normalized)
        return normalized
    return current_llm_usage_session_id()


@contextmanager
def llm_usage_session(session_id: Any):
    normalized = _normalize_session_id(session_id)
    if not normalized:
        yield current_llm_usage_session_id()
        return
    token = _USAGE_SESSION_VAR.set(normalized)
    try:
        yield normalized
    finally:
        _USAGE_SESSION_VAR.reset(token)


def _read_field(payload: Any, *path: str) -> Any:
    current = payload
    for key in path:
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(key)
            continue
        current = getattr(current, key, None)
    return current


def _is_cjk_char(char: str) -> bool:
    code = ord(char)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x20000 <= code <= 0x2A6DF
        or 0x2A700 <= code <= 0x2B73F
        or 0x2B740 <= code <= 0x2B81F
        or 0x2B820 <= code <= 0x2CEAF
        or 0x2CEB0 <= code <= 0x2EBEF
    )


def _looks_like_binary_text(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if text.startswith("data:"):
        return True
    if len(text) < 160:
        return False
    sample = text[:512]
    if " " in sample:
        return False
    return bool(_BASE64_TOKEN_RE.fullmatch(sample))


def _append_estimate_text(chunks: list[str], value: Any) -> None:
    if isinstance(value, _ESTIMATE_SKIP_VALUE_TYPES):
        return
    text = str(value or "").strip()
    if not text or _looks_like_binary_text(text):
        return
    chunks.append(text)


def _collect_estimate_text(
    payload: Any,
    chunks: list[str],
    *,
    key: str = "",
    depth: int = 0,
    seen: set[int] | None = None,
) -> None:
    if payload is None or depth > 12:
        return

    if seen is None:
        seen = set()

    if isinstance(payload, str):
        if key not in _ESTIMATE_SKIP_KEYS:
            _append_estimate_text(chunks, payload)
        return

    if isinstance(payload, _ESTIMATE_SKIP_VALUE_TYPES):
        return

    if isinstance(payload, (int, float, bool, type(None), Path)):
        return

    object_id = id(payload)
    if object_id in seen:
        return
    seen.add(object_id)

    if isinstance(payload, dict):
        for child_key, child_value in payload.items():
            normalized_key = str(child_key or "").strip()
            if normalized_key in _ESTIMATE_SKIP_KEYS:
                continue
            _collect_estimate_text(
                child_value,
                chunks,
                key=normalized_key,
                depth=depth + 1,
                seen=seen,
            )
        return

    if isinstance(payload, (list, tuple, set)):
        for item in payload:
            _collect_estimate_text(
                item,
                chunks,
                key=key,
                depth=depth + 1,
                seen=seen,
            )
        return

    if hasattr(payload, "model_dump") and callable(getattr(payload, "model_dump")):
        try:
            _collect_estimate_text(
                payload.model_dump(),
                chunks,
                key=key,
                depth=depth + 1,
                seen=seen,
            )
            return
        except Exception:
            pass

    if hasattr(payload, "__dict__"):
        try:
            _collect_estimate_text(
                vars(payload),
                chunks,
                key=key,
                depth=depth + 1,
                seen=seen,
            )
            return
        except Exception:
            pass


def _estimate_token_count(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    cjk_chars = sum(1 for char in text if _is_cjk_char(char))
    non_cjk = "".join(char for char in text if not _is_cjk_char(char))
    non_cjk_bytes = len(non_cjk.encode("utf-8"))
    estimate = cjk_chars + math.ceil(non_cjk_bytes / 4)
    if estimate <= 0:
        return 0
    return max(1, estimate)


def _estimate_request_tokens(request_kwargs: dict[str, Any] | None) -> int:
    payload = dict(request_kwargs or {})
    if not payload:
        return 0
    chunks: list[str] = []
    _collect_estimate_text(payload, chunks)
    estimate = sum(_estimate_token_count(chunk) for chunk in chunks)
    messages = payload.get("messages")
    if isinstance(messages, list):
        estimate += max(0, len(messages)) * 4
    if payload.get("tools"):
        estimate += 12
    return estimate


def _extract_response_text(response: Any) -> str:
    chunks: list[str] = []

    direct_text = _read_field(response, "text")
    if isinstance(direct_text, str):
        _append_estimate_text(chunks, direct_text)

    choices = _read_field(response, "choices")
    if isinstance(choices, list):
        for choice in choices:
            _collect_estimate_text(_read_field(choice, "message", "content"), chunks)
            _collect_estimate_text(_read_field(choice, "message", "tool_calls"), chunks)

    for key in ("output", "content", "candidates", "result"):
        _collect_estimate_text(_read_field(response, key), chunks)

    if not chunks:
        _collect_estimate_text(response, chunks)

    return "\n".join(chunk for chunk in chunks if chunk)


def _estimate_usage_metrics(
    *,
    request_kwargs: dict[str, Any] | None,
    response: Any,
    operation: str,
) -> dict[str, Any]:
    normalized_operation = str(operation or "").strip().lower()
    input_tokens = _estimate_request_tokens(request_kwargs)
    output_tokens = 0

    if normalized_operation in {
        "chat.completions.create",
        "responses.create",
        "audio.transcriptions.create",
    }:
        output_tokens = _estimate_token_count(_extract_response_text(response))
    elif normalized_operation in {"embeddings.create", "audio.speech.create"}:
        output_tokens = 0
    elif normalized_operation.startswith("images."):
        output_tokens = 0
    else:
        output_tokens = _estimate_token_count(_extract_response_text(response))

    total_tokens = input_tokens + output_tokens
    if total_tokens <= 0:
        return {
            "estimated": False,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
    return {
        "estimated": True,
        "input_tokens": max(0, input_tokens),
        "output_tokens": max(0, output_tokens),
        "total_tokens": max(0, total_tokens),
    }


def _first_int(payload: Any, *paths: tuple[str, ...]) -> int:
    for path in paths:
        value = _read_field(payload, *path)
        if value is None:
            continue
        if isinstance(value, bool):
            continue
        try:
            return int(value)
        except Exception:
            continue
    return 0


def _extract_usage_metrics(
    response: Any,
    *,
    request_kwargs: dict[str, Any] | None = None,
    operation: str = "",
) -> dict[str, Any]:
    usage = _read_field(response, "usage")
    usage_present = usage is not None
    input_tokens = _first_int(
        usage,
        ("prompt_tokens",),
        ("input_tokens",),
    )
    output_tokens = _first_int(
        usage,
        ("completion_tokens",),
        ("output_tokens",),
    )
    total_tokens = _first_int(
        usage,
        ("total_tokens",),
    )
    cache_read_tokens = _first_int(
        usage,
        ("prompt_tokens_details", "cached_tokens"),
        ("input_tokens_details", "cached_tokens"),
        ("cache_read_input_tokens",),
        ("input_cached_tokens",),
        ("cached_tokens",),
    )
    cache_write_tokens = _first_int(
        usage,
        ("prompt_tokens_details", "cache_creation_tokens"),
        ("input_tokens_details", "cache_creation_tokens"),
        ("cache_creation_input_tokens",),
        ("cache_write_input_tokens",),
    )
    if total_tokens <= 0 and (input_tokens > 0 or output_tokens > 0):
        total_tokens = input_tokens + output_tokens

    estimated = False
    if not usage_present:
        estimated_metrics = _estimate_usage_metrics(
            request_kwargs=request_kwargs,
            response=response,
            operation=operation,
        )
        estimated = bool(estimated_metrics.get("estimated"))
        if estimated:
            input_tokens = max(input_tokens, _int_value(estimated_metrics.get("input_tokens")))
            output_tokens = max(
                output_tokens,
                _int_value(estimated_metrics.get("output_tokens")),
            )
            total_tokens = max(total_tokens, _int_value(estimated_metrics.get("total_tokens")))

    return {
        "usage_present": bool(usage_present),
        "estimated": estimated,
        "input_tokens": max(0, input_tokens),
        "output_tokens": max(0, output_tokens),
        "total_tokens": max(0, total_tokens),
        "cache_read_tokens": max(0, cache_read_tokens),
        "cache_write_tokens": max(0, cache_write_tokens),
        "cache_hit": bool(cache_read_tokens > 0),
    }


def _candidate_model_keys(raw_model: str) -> list[str]:
    token = str(raw_model or "").strip()
    if not token:
        return []
    load_models_config()
    cfg = get_models_config()
    if cfg is None:
        return []

    matches: list[str] = []
    for model_key in cfg.list_models():
        if model_key == token:
            matches.append(model_key)
            continue
        try:
            if get_model_id_for_api(model_key) == token:
                matches.append(model_key)
        except Exception:
            continue
    return matches


def _resolve_model_key(default_model_key: str, request_model: Any) -> str:
    default_token = str(default_model_key or "").strip()
    request_token = str(request_model or "").strip()

    if request_token and "/" in request_token:
        return request_token
    if default_token and "/" in default_token and not request_token:
        return default_token
    if default_token and "/" in default_token and request_token:
        try:
            if get_model_id_for_api(default_token) == request_token:
                return default_token
        except Exception:
            pass

    matches = _candidate_model_keys(request_token or default_token)
    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1 and default_token and "/" in default_token:
        provider = default_token.split("/", 1)[0]
        for match in matches:
            if match.split("/", 1)[0] == provider:
                return match

    if request_token:
        return request_token
    if default_token:
        return default_token
    return "unknown"


def _blank_summary_row(model_key: str) -> dict[str, Any]:
    return {
        "model_key": str(model_key or "unknown"),
        "requests": 0,
        "success_requests": 0,
        "failed_requests": 0,
        "usage_requests": 0,
        "missing_usage_requests": 0,
        "estimated_token_requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cache_hit_requests": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }


class LlmUsageStore:
    def __init__(self) -> None:
        self.db_path = (Path(DATA_DIR).resolve() / "bot_data.db").resolve()
        self._lock = Lock()
        self._db_ready = False

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_db(self) -> None:
        if self._db_ready:
            return
        with self._lock:
            if self._db_ready:
                return
            with self._connect() as conn:
                conn.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {_USAGE_TABLE} (
                        day TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        model_key TEXT NOT NULL,
                        requests INTEGER NOT NULL DEFAULT 0,
                        success_requests INTEGER NOT NULL DEFAULT 0,
                        failed_requests INTEGER NOT NULL DEFAULT 0,
                        usage_requests INTEGER NOT NULL DEFAULT 0,
                        missing_usage_requests INTEGER NOT NULL DEFAULT 0,
                        estimated_token_requests INTEGER NOT NULL DEFAULT 0,
                        input_tokens INTEGER NOT NULL DEFAULT 0,
                        output_tokens INTEGER NOT NULL DEFAULT 0,
                        total_tokens INTEGER NOT NULL DEFAULT 0,
                        cache_hit_requests INTEGER NOT NULL DEFAULT 0,
                        cache_read_tokens INTEGER NOT NULL DEFAULT 0,
                        cache_write_tokens INTEGER NOT NULL DEFAULT 0,
                        first_used_at TEXT NOT NULL,
                        last_used_at TEXT NOT NULL,
                        PRIMARY KEY (day, session_id, model_key)
                    )
                    """
                )
                conn.execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS idx_{_USAGE_TABLE}_day_model
                    ON {_USAGE_TABLE} (day, model_key)
                    """
                )
                conn.execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS idx_{_USAGE_TABLE}_last_used
                    ON {_USAGE_TABLE} (last_used_at)
                    """
                )
            self._db_ready = True

    def record_event(
        self,
        *,
        operation: str,
        default_model_key: str,
        request_kwargs: dict[str, Any] | None,
        response: Any = None,
        success: bool,
        error: Exception | None = None,
    ) -> None:
        _ = error
        request = dict(request_kwargs or {})
        requested_model = str(request.get("model") or "").strip()
        model_key = _resolve_model_key(default_model_key, requested_model)
        session_id = current_llm_usage_session_id()
        metrics = {}
        if response is not None:
            metrics = _extract_usage_metrics(
                response,
                request_kwargs=request,
                operation=operation,
            )

        ts = _now_iso()
        day = ts[:10]
        increments = {
            "requests": 1,
            "success_requests": 1 if success else 0,
            "failed_requests": 0 if success else 1,
            "usage_requests": 1 if metrics.get("usage_present") else 0,
            "missing_usage_requests": 0 if metrics.get("usage_present") else 1,
            "estimated_token_requests": 1 if metrics.get("estimated") else 0,
            "input_tokens": _int_value(metrics.get("input_tokens")),
            "output_tokens": _int_value(metrics.get("output_tokens")),
            "total_tokens": _int_value(metrics.get("total_tokens")),
            "cache_hit_requests": 1 if metrics.get("cache_hit") else 0,
            "cache_read_tokens": _int_value(metrics.get("cache_read_tokens")),
            "cache_write_tokens": _int_value(metrics.get("cache_write_tokens")),
        }

        columns = [
            "day",
            "session_id",
            "model_key",
            "requests",
            "success_requests",
            "failed_requests",
            "usage_requests",
            "missing_usage_requests",
            "estimated_token_requests",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cache_hit_requests",
            "cache_read_tokens",
            "cache_write_tokens",
            "first_used_at",
            "last_used_at",
        ]
        placeholders = ", ".join(["?"] * len(columns))
        update_columns = [
            "requests",
            "success_requests",
            "failed_requests",
            "usage_requests",
            "missing_usage_requests",
            "estimated_token_requests",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cache_hit_requests",
            "cache_read_tokens",
            "cache_write_tokens",
        ]
        assignments = ", ".join(
            f"{column} = {_USAGE_TABLE}.{column} + excluded.{column}"
            for column in update_columns
        )
        assignments += ", last_used_at = excluded.last_used_at"

        values = [
            day,
            session_id,
            model_key,
            increments["requests"],
            increments["success_requests"],
            increments["failed_requests"],
            increments["usage_requests"],
            increments["missing_usage_requests"],
            increments["estimated_token_requests"],
            increments["input_tokens"],
            increments["output_tokens"],
            increments["total_tokens"],
            increments["cache_hit_requests"],
            increments["cache_read_tokens"],
            increments["cache_write_tokens"],
            ts,
            ts,
        ]

        self._ensure_db()
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute(
                        f"""
                        INSERT INTO {_USAGE_TABLE} ({", ".join(columns)})
                        VALUES ({placeholders})
                        ON CONFLICT(day, session_id, model_key)
                        DO UPDATE SET {assignments}
                        """,
                        values,
                    )
            except Exception:
                logger.debug("Failed to persist llm usage row", exc_info=True)

    def summarize(self, *, day: str | None = None) -> dict[str, Any]:
        self._ensure_db()
        overall = _blank_summary_row("overall")
        overall["models"] = []
        overall["last_event_at"] = ""

        where_sql = ""
        params: list[Any] = []
        if day:
            where_sql = "WHERE day = ?"
            params.append(str(day))

        with self._lock:
            with self._connect() as conn:
                overall_row = conn.execute(
                    f"""
                    SELECT
                        COALESCE(SUM(requests), 0) AS requests,
                        COALESCE(SUM(success_requests), 0) AS success_requests,
                        COALESCE(SUM(failed_requests), 0) AS failed_requests,
                        COALESCE(SUM(usage_requests), 0) AS usage_requests,
                        COALESCE(SUM(missing_usage_requests), 0) AS missing_usage_requests,
                        COALESCE(SUM(estimated_token_requests), 0) AS estimated_token_requests,
                        COALESCE(SUM(input_tokens), 0) AS input_tokens,
                        COALESCE(SUM(output_tokens), 0) AS output_tokens,
                        COALESCE(SUM(total_tokens), 0) AS total_tokens,
                        COALESCE(SUM(cache_hit_requests), 0) AS cache_hit_requests,
                        COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
                        COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens,
                        COALESCE(MAX(last_used_at), '') AS last_event_at
                    FROM {_USAGE_TABLE}
                    {where_sql}
                    """,
                    params,
                ).fetchone()
                if overall_row is None:
                    return overall

                for key in (
                    "requests",
                    "success_requests",
                    "failed_requests",
                    "usage_requests",
                    "missing_usage_requests",
                    "estimated_token_requests",
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                    "cache_hit_requests",
                    "cache_read_tokens",
                    "cache_write_tokens",
                ):
                    overall[key] = _int_value(overall_row[key])
                overall["last_event_at"] = str(overall_row["last_event_at"] or "").strip()

                rows = conn.execute(
                    f"""
                    SELECT
                        model_key,
                        COALESCE(SUM(requests), 0) AS requests,
                        COALESCE(SUM(success_requests), 0) AS success_requests,
                        COALESCE(SUM(failed_requests), 0) AS failed_requests,
                        COALESCE(SUM(usage_requests), 0) AS usage_requests,
                        COALESCE(SUM(missing_usage_requests), 0) AS missing_usage_requests,
                        COALESCE(SUM(estimated_token_requests), 0) AS estimated_token_requests,
                        COALESCE(SUM(input_tokens), 0) AS input_tokens,
                        COALESCE(SUM(output_tokens), 0) AS output_tokens,
                        COALESCE(SUM(total_tokens), 0) AS total_tokens,
                        COALESCE(SUM(cache_hit_requests), 0) AS cache_hit_requests,
                        COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
                        COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens
                    FROM {_USAGE_TABLE}
                    {where_sql}
                    GROUP BY model_key
                    ORDER BY total_tokens DESC, requests DESC, model_key ASC
                    """
                    ,
                    params,
                ).fetchall()

        overall["models"] = [
            {
                "model_key": str(row["model_key"] or "unknown").strip() or "unknown",
                "requests": _int_value(row["requests"]),
                "success_requests": _int_value(row["success_requests"]),
                "failed_requests": _int_value(row["failed_requests"]),
                "usage_requests": _int_value(row["usage_requests"]),
                "missing_usage_requests": _int_value(row["missing_usage_requests"]),
                "estimated_token_requests": _int_value(row["estimated_token_requests"]),
                "input_tokens": _int_value(row["input_tokens"]),
                "output_tokens": _int_value(row["output_tokens"]),
                "total_tokens": _int_value(row["total_tokens"]),
                "cache_hit_requests": _int_value(row["cache_hit_requests"]),
                "cache_read_tokens": _int_value(row["cache_read_tokens"]),
                "cache_write_tokens": _int_value(row["cache_write_tokens"]),
            }
            for row in rows
        ]
        return overall

    def render_summary(
        self,
        *,
        limit: int = 12,
        day: str | None = None,
        title: str = "📊 LLM Token 用量",
        empty_text: str = "📊 暂无 LLM 用量统计。",
    ) -> str:
        summary = self.summarize(day=day)
        rows = list(summary.get("models") or [])
        if not rows:
            return empty_text

        shown = rows[: max(1, limit)]
        lines = [
            title,
            "",
            "- 说明：按天 + 会话 + 模型聚合存储；上游未返回 usage 时，token 使用本地估算；缓存统计只计上游实际返回值。",
            f"- 总请求：`{summary.get('requests', 0)}`",
            f"- 成功请求：`{summary.get('success_requests', 0)}`",
            f"- 失败请求：`{summary.get('failed_requests', 0)}`",
            f"- 返回 usage 的请求：`{summary.get('usage_requests', 0)}`",
            f"- 无 usage 的请求：`{summary.get('missing_usage_requests', 0)}`",
            f"- 本地估算 token 的请求：`{summary.get('estimated_token_requests', 0)}`",
            f"- 输入 tokens：`{summary.get('input_tokens', 0)}`",
            f"- 输出 tokens：`{summary.get('output_tokens', 0)}`",
            f"- 总 tokens：`{summary.get('total_tokens', 0)}`",
            f"- 缓存命中请求：`{summary.get('cache_hit_requests', 0)}`",
            f"- 缓存命中 tokens：`{summary.get('cache_read_tokens', 0)}`",
            f"- 缓存写入 tokens：`{summary.get('cache_write_tokens', 0)}`",
        ]
        last_event_at = str(summary.get("last_event_at") or "").strip()
        if last_event_at:
            lines.append(f"- 最后记录时间：`{last_event_at}`")

        lines.extend(["", "按模型："])
        for row in shown:
            lines.append(
                f"- `{row['model_key']}` | req={row['requests']} | ok={row['success_requests']} | "
                f"est={row['estimated_token_requests']} | in={row['input_tokens']} | "
                f"out={row['output_tokens']} | total={row['total_tokens']} | "
                f"cache_hit={row['cache_hit_requests']} | cache_read={row['cache_read_tokens']}"
            )
        if len(rows) > len(shown):
            lines.append(f"- 其余 `{len(rows) - len(shown)}` 个模型未展开")
        return "\n".join(lines)

    def render_today_summary(self, *, limit: int = 12) -> str:
        return self.render_summary(
            limit=limit,
            day=_today_iso(),
            title="📊 今日 LLM Token 用量",
            empty_text="📊 今日暂无 LLM 用量统计。",
        )

    def reset(self) -> int:
        self._ensure_db()
        with self._lock:
            with self._connect() as conn:
                count_row = conn.execute(
                    f"SELECT COUNT(*) AS count FROM {_USAGE_TABLE}"
                ).fetchone()
                count = _int_value(count_row["count"] if count_row else 0)
                conn.execute(f"DELETE FROM {_USAGE_TABLE}")
        return count


class _TrackedOpenAIProxy:
    def __init__(
        self,
        target: Any,
        *,
        default_model_key: str,
        path: tuple[str, ...] = (),
    ) -> None:
        self._target = target
        self._default_model_key = str(default_model_key or "").strip()
        self._path = tuple(path)

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._target, name)
        path = self._path + (str(name),)

        if path in _TRACKED_METHOD_PATHS and callable(target):
            operation = _TRACKED_METHOD_PATHS[path]
            return self._wrap_tracked_method(target, operation)

        if callable(target):
            return target

        if isinstance(target, _WRAP_EXCLUDED_TYPES):
            return target

        return _TrackedOpenAIProxy(
            target,
            default_model_key=self._default_model_key,
            path=path,
        )

    def _record_failure(
        self,
        *,
        operation: str,
        request_kwargs: dict[str, Any],
        error: Exception,
    ) -> None:
        try:
            llm_usage_store.record_event(
                operation=operation,
                default_model_key=self._default_model_key,
                request_kwargs=request_kwargs,
                success=False,
                error=error,
            )
        except Exception:
            logger.debug("Failed to persist llm usage failure row", exc_info=True)

    def _record_success(
        self,
        *,
        operation: str,
        request_kwargs: dict[str, Any],
        response: Any,
    ) -> None:
        try:
            llm_usage_store.record_event(
                operation=operation,
                default_model_key=self._default_model_key,
                request_kwargs=request_kwargs,
                response=response,
                success=True,
            )
        except Exception:
            logger.debug("Failed to persist llm usage row", exc_info=True)

    def _wrap_tracked_method(self, method: Any, operation: str) -> Any:
        if inspect.iscoroutinefunction(method):

            async def _async_wrapper(*args, **kwargs):
                try:
                    response = await method(*args, **kwargs)
                except Exception as exc:
                    self._record_failure(
                        operation=operation,
                        request_kwargs=dict(kwargs),
                        error=exc,
                    )
                    raise
                self._record_success(
                    operation=operation,
                    request_kwargs=dict(kwargs),
                    response=response,
                )
                return response

            return _async_wrapper

        def _sync_wrapper(*args, **kwargs):
            request_kwargs = dict(kwargs)
            try:
                response = method(*args, **kwargs)
            except Exception as exc:
                self._record_failure(
                    operation=operation,
                    request_kwargs=request_kwargs,
                    error=exc,
                )
                raise

            if inspect.isawaitable(response):

                async def _awaitable_wrapper():
                    try:
                        awaited_response = await response
                    except Exception as exc:
                        self._record_failure(
                            operation=operation,
                            request_kwargs=request_kwargs,
                            error=exc,
                        )
                        raise
                    self._record_success(
                        operation=operation,
                        request_kwargs=request_kwargs,
                        response=awaited_response,
                    )
                    return awaited_response

                return _awaitable_wrapper()

            self._record_success(
                operation=operation,
                request_kwargs=request_kwargs,
                response=response,
            )
            return response

        return _sync_wrapper


def wrap_openai_client(client: Any, *, default_model_key: str) -> Any:
    if client is None:
        return None
    return _TrackedOpenAIProxy(client, default_model_key=default_model_key)


llm_usage_store = LlmUsageStore()
