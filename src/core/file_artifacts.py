from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

_PHOTO_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
}
_VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".webm",
    ".mkv",
    ".avi",
    ".m4v",
}
_AUDIO_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
    ".ogg",
    ".flac",
}
_DOCUMENT_EXTENSIONS = {
    ".pdf",
    ".md",
    ".txt",
    ".csv",
    ".tsv",
    ".xlsx",
    ".xls",
    ".json",
    ".html",
    ".htm",
    ".zip",
}
_PATH_EXTENSIONS = tuple(
    sorted(
        _PHOTO_EXTENSIONS
        | _VIDEO_EXTENSIONS
        | _AUDIO_EXTENSIONS
        | _DOCUMENT_EXTENSIONS
    )
)
_SAVED_FILE_RE = re.compile(r"(?im)^\s*saved_file=(?P<path>.+?)\s*$")
_LABELED_PATH_RE = re.compile(
    r"(?im)^\s*(?:保存路径|文件路径|图片路径|输出路径|附件路径|图片已保存至|save(?:d)?[_ ]file|saved to|output file|file path)\s*[:：=]?\s*`?(?P<path>/[^`\n]+?)`?\s*$"
)
_BACKTICK_PATH_RE = re.compile(
    r"`(?P<path>/[^`\n]+?(?:"
    + "|".join(re.escape(ext) for ext in _PATH_EXTENSIONS)
    + r"))`",
    flags=re.IGNORECASE,
)
_RAW_PATH_RE = re.compile(
    r"(?P<path>/[^\s`\"'<>]+?(?:"
    + "|".join(re.escape(ext) for ext in _PATH_EXTENSIONS)
    + r"))(?=$|[\s),.;:!?])",
    flags=re.IGNORECASE,
)
_DEFAULT_MAX_BYTES = max(
    1,
    int(os.getenv("AUTO_DELIVERY_MAX_FILE_MB", "49")) * 1024 * 1024,
)


def classify_file_kind(path_or_name: str) -> str:
    suffix = Path(str(path_or_name or "")).suffix.lower()
    if suffix in _PHOTO_EXTENSIONS:
        return "photo"
    if suffix in _VIDEO_EXTENSIONS:
        return "video"
    if suffix in _AUDIO_EXTENSIONS:
        return "audio"
    return "document"


def normalize_file_rows(
    raw_files: Any,
    *,
    max_size_bytes: int | None = _DEFAULT_MAX_BYTES,
    limit: int = 8,
) -> list[dict[str, str]]:
    if not isinstance(raw_files, list):
        return []

    normalized: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    seen_names: set[tuple[str, str]] = set()
    for item in raw_files:
        if not isinstance(item, dict):
            continue
        path_text = _normalize_candidate_path(item.get("path"))
        if not path_text:
            continue
        row = _build_file_row(
            path_text,
            kind=str(item.get("kind") or "").strip().lower() or None,
            filename=str(item.get("filename") or "").strip() or None,
            caption=str(item.get("caption") or "").strip()[:500] or None,
            max_size_bytes=max_size_bytes,
        )
        if row is None:
            continue
        path_key = str(row["path"])
        name_key = (str(row["kind"]), str(row["filename"]))
        if path_key in seen_paths or name_key in seen_names:
            continue
        seen_paths.add(path_key)
        seen_names.add(name_key)
        normalized.append(row)
        if len(normalized) >= max(1, int(limit)):
            break
    return normalized


def merge_file_rows(*groups: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    seen_names: set[tuple[str, str]] = set()
    for group in groups:
        for item in list(group or []):
            if not isinstance(item, dict):
                continue
            path_text = str(item.get("path") or "").strip()
            kind = str(item.get("kind") or "document").strip().lower() or "document"
            filename = str(item.get("filename") or "").strip()
            if not path_text or not filename:
                continue
            path_key = path_text
            name_key = (kind, filename)
            if path_key in seen_paths or name_key in seen_names:
                continue
            seen_paths.add(path_key)
            seen_names.add(name_key)
            merged.append(
                {
                    "kind": kind,
                    "path": path_key,
                    "filename": filename,
                    "caption": str(item.get("caption") or "").strip()[:500],
                }
            )
    return merged


def extract_saved_file_rows(
    text: str,
    *,
    max_size_bytes: int | None = _DEFAULT_MAX_BYTES,
    limit: int = 8,
) -> list[dict[str, str]]:
    return _extract_file_rows_from_matches(
        text,
        patterns=(_SAVED_FILE_RE,),
        allow_any_extension=True,
        max_size_bytes=max_size_bytes,
        limit=limit,
    )


def extract_file_rows_from_text(
    text: str,
    *,
    max_size_bytes: int | None = _DEFAULT_MAX_BYTES,
    limit: int = 8,
) -> list[dict[str, str]]:
    explicit = _extract_file_rows_from_matches(
        text,
        patterns=(_SAVED_FILE_RE, _LABELED_PATH_RE),
        allow_any_extension=True,
        max_size_bytes=max_size_bytes,
        limit=limit,
    )
    if len(explicit) >= max(1, int(limit)):
        return explicit[:limit]

    hinted = _extract_file_rows_from_matches(
        text,
        patterns=(_BACKTICK_PATH_RE, _RAW_PATH_RE),
        allow_any_extension=False,
        max_size_bytes=max_size_bytes,
        limit=max(1, int(limit)) - len(explicit),
    )
    return merge_file_rows(explicit, hinted)[: max(1, int(limit))]


def strip_saved_file_markers(text: str) -> str:
    raw = str(text or "")
    if not raw:
        return ""
    cleaned = _SAVED_FILE_RE.sub("", raw)
    cleaned_lines = [line.rstrip() for line in cleaned.splitlines()]
    while cleaned_lines and not cleaned_lines[0].strip():
        cleaned_lines.pop(0)
    while cleaned_lines and not cleaned_lines[-1].strip():
        cleaned_lines.pop()
    return "\n".join(cleaned_lines).strip()


def _extract_file_rows_from_matches(
    text: str,
    *,
    patterns: tuple[re.Pattern[str], ...],
    allow_any_extension: bool,
    max_size_bytes: int | None,
    limit: int,
) -> list[dict[str, str]]:
    raw = str(text or "")
    if not raw:
        return []

    rows: list[dict[str, str]] = []
    seen_candidates: set[str] = set()
    for pattern in patterns:
        for match in pattern.finditer(raw):
            path_text = _normalize_candidate_path(match.group("path"))
            if not path_text or path_text in seen_candidates:
                continue
            seen_candidates.add(path_text)
            row = _build_file_row(
                path_text,
                kind=None,
                filename=None,
                caption=None,
                max_size_bytes=max_size_bytes,
                allow_any_extension=allow_any_extension,
            )
            if row is None:
                continue
            rows.append(row)
            if len(rows) >= max(1, int(limit)):
                return rows
    return rows


def _normalize_candidate_path(value: Any) -> str:
    text = str(value or "").strip().strip("`").strip('"').strip("'").strip()
    while text and text[-1] in ",.;:!?)】」】":
        text = text[:-1].rstrip()
    return text


def _build_file_row(
    path_text: str,
    *,
    kind: str | None,
    filename: str | None,
    caption: str | None,
    max_size_bytes: int | None,
    allow_any_extension: bool = True,
) -> dict[str, str] | None:
    if not path_text:
        return None
    try:
        path_obj = Path(path_text).expanduser().resolve()
    except Exception:
        return None
    if not path_obj.exists() or not path_obj.is_file():
        return None
    if not allow_any_extension and path_obj.suffix.lower() not in _PATH_EXTENSIONS:
        return None
    if max_size_bytes and path_obj.stat().st_size > int(max_size_bytes):
        return None

    resolved_kind = str(kind or "").strip().lower() or classify_file_kind(path_obj.name)
    if resolved_kind not in {"photo", "video", "audio", "document"}:
        resolved_kind = classify_file_kind(path_obj.name)
    resolved_filename = str(filename or path_obj.name).strip() or path_obj.name
    return {
        "kind": resolved_kind,
        "path": str(path_obj),
        "filename": resolved_filename,
        "caption": str(caption or "").strip()[:500],
    }
