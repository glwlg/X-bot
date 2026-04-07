from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import mimetypes
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx

from core.config import get_client_for_model, is_user_allowed
from core.media_hooks import IncomingMediaInterceptResult, ReplyContextHookResult
from core.model_config import (
    get_model_candidates_for_input,
    get_voice_model,
    select_model_for_role,
)
from core.platform.exceptions import MediaProcessingError
from core.platform.models import MessageType, UnifiedContext, UnifiedMessage
from handlers.base_handlers import require_feature_access
from handlers.media_utils import extract_media_input
from services.openai_adapter import generate_text

from extension.skills.builtin.download_video.scripts.services.download_service import (
    get_download_dir,
)
from extension.skills.builtin.download_video.scripts.store import get_video_cache

try:
    from .store import get_cached_artifact, save_cached_artifact
except ImportError:
    from store import get_cached_artifact, save_cached_artifact

logger = logging.getLogger(__name__)
_FATAL_AUDIO_DIAGNOSTIC_PREFIX = "fatal audio transcription error: "
_WHISPER_HTTP_MODEL = "whisper_http"
ProgressCallback = Callable[[str], None]


def _env_int(name: str, default: int, minimum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = default
    return max(minimum, value)


def _env_float(name: str, default: float, minimum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except Exception:
        value = default
    return max(minimum, value)


def _audio_request_timeout_seconds() -> float:
    return _env_float("VIDEO_TO_TEXT_AUDIO_REQUEST_TIMEOUT_SECONDS", 30.0, 5.0)


def _whisper_http_endpoint() -> str:
    return str(
        os.getenv("VIDEO_TO_TEXT_WHISPER_ENDPOINT")
        or os.getenv("WHISPER_INFERENCE_URL")
        or ""
    ).strip()


def _whisper_http_enabled() -> bool:
    return bool(_whisper_http_endpoint())


def _whisper_http_timeout_seconds() -> float:
    return _env_float("VIDEO_TO_TEXT_WHISPER_TIMEOUT_SECONDS", 180.0, 5.0)


def _whisper_http_response_format() -> str:
    return str(os.getenv("VIDEO_TO_TEXT_WHISPER_RESPONSE_FORMAT") or "json").strip() or "json"


def _whisper_http_language() -> str:
    return str(os.getenv("VIDEO_TO_TEXT_WHISPER_LANGUAGE") or "zh").strip()


def video_to_text_enabled() -> bool:
    return _whisper_http_enabled()


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "1" if default else "0")).strip().lower()
    return raw in {"1", "true", "yes", "y", "on"}


def _whisper_http_temperature() -> float:
    return _env_float("VIDEO_TO_TEXT_WHISPER_TEMPERATURE", 0.0, 0.0)


def _whisper_http_temperature_inc() -> float:
    return _env_float("VIDEO_TO_TEXT_WHISPER_TEMPERATURE_INC", 0.2, 0.0)


def _whisper_http_no_timestamps() -> bool:
    return _env_bool("VIDEO_TO_TEXT_WHISPER_NO_TIMESTAMPS", True)


def _whisper_http_prefer_full_audio() -> bool:
    return _env_bool("VIDEO_TO_TEXT_WHISPER_PREFER_FULL_AUDIO", True)


def _whisper_http_max_full_audio_seconds() -> float:
    return _env_float("VIDEO_TO_TEXT_WHISPER_MAX_FULL_AUDIO_SECONDS", 900.0, 60.0)


def _estimated_base64_size(binary_size: int) -> int:
    safe_size = max(0, int(binary_size or 0))
    return ((safe_size + 2) // 3) * 4


def _estimated_audio_request_size(audio_size_bytes: int) -> int:
    # Reserve a small constant for the surrounding JSON payload.
    return _estimated_base64_size(audio_size_bytes) + 4096


def _audio_request_too_large(audio_size_bytes: int) -> bool:
    max_request_bytes = _env_int(
        "VIDEO_TO_TEXT_AUDIO_MAX_REQUEST_BYTES",
        6_291_456,
        1_048_576,
    )
    return _estimated_audio_request_size(audio_size_bytes) > max_request_bytes


def _request_body_too_large_error(error_text: str) -> bool:
    safe_text = str(error_text or "").lower()
    return (
        "exceeded limit on max bytes to request body" in safe_text
        or "request body" in safe_text
        and "max bytes" in safe_text
    )


def _fatal_audio_diagnostic(detail: str) -> str:
    safe_detail = str(detail or "").strip() or "audio transcription failed"
    return f"{_FATAL_AUDIO_DIAGNOSTIC_PREFIX}{safe_detail}"


def _extract_fatal_audio_error(diagnostics: list[str]) -> str:
    for item in diagnostics or []:
        safe_item = str(item or "").strip()
        if safe_item.startswith(_FATAL_AUDIO_DIAGNOSTIC_PREFIX):
            return safe_item[len(_FATAL_AUDIO_DIAGNOSTIC_PREFIX) :].strip()
    return ""


def _invalid_audio_part_type_error(error_text: str) -> bool:
    safe_text = str(error_text or "").lower()
    return (
        "invalid value: file" in safe_text
        or "invalid value: input_audio" in safe_text
    ) and "supported values are" in safe_text


def _audio_part_style_parameter_error(error_text: str) -> bool:
    safe_text = str(error_text or "").lower()
    return (
        "provided url does not appear to be valid" in safe_text
        or "input[0].content[1].file_data" in safe_text
        or "messages.[0].content[1].file_data" in safe_text
        or "content[1].file_data" in safe_text
        or "input[0].content[1].input_audio.data" in safe_text
        or "invalidparameter" in safe_text
        and "url" in safe_text
        or "invalid_parameter_error" in safe_text
        and "url" in safe_text
    )


def _candidate_audio_part_styles() -> list[str]:
    explicit = str(os.getenv("OPENAI_AUDIO_PART_STYLE") or "").strip().lower()
    ordered: list[str] = []
    supported_styles = ("file", "input_audio", "input_audio_data_uri")
    if explicit in supported_styles:
        ordered.append(explicit)
    configured_voice_model = str(get_voice_model() or "").strip().lower()
    default_style = explicit if explicit in supported_styles else "file"
    if explicit not in supported_styles and "gpt" in configured_voice_model:
        default_style = "input_audio"
    for style in (default_style, "file", "input_audio", "input_audio_data_uri"):
        if style not in ordered:
            ordered.append(style)
    return ordered


def _candidate_voice_models() -> list[str]:
    ordered: list[str] = []

    def add(model_key: str) -> None:
        safe_model_key = str(model_key or "").strip()
        if safe_model_key and safe_model_key not in ordered:
            ordered.append(safe_model_key)

    preferred_voice_model = str(get_voice_model() or "").strip()
    for pool_type in ("voice", "routing", "primary", "vision"):
        for model_key in get_model_candidates_for_input(
            "voice",
            pool_type=pool_type,
            preferred_model=preferred_voice_model,
        ):
            add(model_key)
    return ordered


def _resolve_audio_transcription_target() -> tuple[str, Any, list[str]]:
    diagnostics: list[str] = []
    configured_voice_model = str(get_voice_model() or "").strip()
    candidate_models = _candidate_voice_models()

    if configured_voice_model and configured_voice_model not in candidate_models:
        diagnostics.append(
            f"configured voice model {configured_voice_model} does not advertise voice input"
        )

    for model_key in candidate_models:
        client = get_client_for_model(model_key, is_async=True)
        if client is not None:
            if configured_voice_model and model_key != configured_voice_model:
                diagnostics.append(
                    "audio transcription using "
                    f"{model_key} instead of configured {configured_voice_model}"
                )
            else:
                diagnostics.append(f"audio transcription model selected: {model_key}")
            return model_key, client, diagnostics
        diagnostics.append(f"audio transcription client unavailable: {model_key}")

    if not candidate_models:
        if configured_voice_model:
            diagnostics.append(
                f"no voice-capable model available for configured voice model {configured_voice_model}"
            )
        else:
            diagnostics.append("no voice-capable model available")
    return "", None, diagnostics


def _available_audio_transcription_targets() -> list[tuple[str, Any]]:
    targets: list[tuple[str, Any]] = []
    for model_key in _candidate_voice_models():
        client = get_client_for_model(model_key, is_async=True)
        if client is None:
            continue
        targets.append((model_key, client))
    return targets


def _next_smaller_audio_segment_seconds(
    current_seconds: float,
    *,
    minimum_seconds: float,
) -> float | None:
    safe_current = max(0.0, float(current_seconds or 0.0))
    safe_minimum = max(1.0, float(minimum_seconds or 1.0))
    if safe_current <= safe_minimum + 0.001:
        return None
    reduced = max(safe_minimum, round(safe_current / 2.0, 3))
    if reduced >= safe_current:
        return None
    return reduced


def _next_smaller_segment_seconds(
    current_seconds: float,
    *,
    minimum_seconds: float,
) -> float | None:
    return _next_smaller_audio_segment_seconds(
        current_seconds,
        minimum_seconds=minimum_seconds,
    )


@dataclass(slots=True)
class VideoMetadata:
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    video_codec: str = ""
    audio_codec: str = ""
    mime_type: str = ""


@dataclass(slots=True)
class FrameSample:
    index: int
    timestamp_seconds: float
    image_path: str
    mime_type: str = "image/jpeg"
    description: str = ""
    visible_text: str = ""
    error: str = ""


@dataclass(slots=True)
class TranscriptSegment:
    index: int
    start_seconds: float
    end_seconds: float
    transcript: str = ""
    status: str = ""
    error: str = ""


@dataclass(slots=True)
class AudioTranscriptionStrategy:
    model: str
    audio_part_style: str
    mime_type: str
    source_kind: str


@dataclass(slots=True)
class AudioTranscriptionState:
    locked_strategy: AudioTranscriptionStrategy | None = None


def _audio_strategy_label(strategy: AudioTranscriptionStrategy | None) -> str:
    if strategy is None:
        return ""
    return (
        f"model={strategy.model} "
        f"style={strategy.audio_part_style} "
        f"mime={strategy.mime_type} "
        f"source={strategy.source_kind}"
    )


def _status_locks_audio_strategy(status: str) -> bool:
    return str(status or "").strip().lower() in {
        "transcribed",
        "no_audio",
        "unintelligible",
    }


@dataclass(slots=True)
class VideoTextResult:
    ok: bool
    artifact_path: str = ""
    source_video_path: str = ""
    mime_type: str = ""
    workspace_path: str = ""
    audio_track_path: str = ""
    segment_audio_dir: str = ""
    segment_text_dir: str = ""
    progress_log_path: str = ""
    metadata: VideoMetadata = field(default_factory=VideoMetadata)
    frame_count: int = 0
    transcript_segment_count: int = 0
    diagnostics: list[str] = field(default_factory=list)
    from_cache: bool = False
    audio_incomplete: bool = False
    cached_file_id: str = ""
    cached_platform: str = ""


def _downloads_root() -> Path:
    target = Path(get_download_dir()).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    return target


def _transcripts_dir() -> Path:
    target = _downloads_root() / "transcripts"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _video_inputs_dir() -> Path:
    target = _downloads_root() / "video_inputs"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _safe_slug(value: str, default: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(value or "").strip()).strip("._-")
    return cleaned or default


def _video_suffix_from_mime(mime_type: str) -> str:
    base = str(mime_type or "").split(";", 1)[0].strip().lower()
    mapping = {
        "video/mp4": ".mp4",
        "video/webm": ".webm",
        "video/quicktime": ".mov",
        "video/x-matroska": ".mkv",
        "video/mpeg": ".mpeg",
        "video/avi": ".avi",
        "video/x-msvideo": ".avi",
        "video/ogg": ".ogv",
    }
    guessed = mimetypes.guess_extension(base or "")
    return mapping.get(base, guessed or ".mp4")


def _artifact_path_for_video(video_path: Path) -> Path:
    stat = video_path.stat()
    digest = hashlib.sha1(
        f"{video_path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")
    ).hexdigest()[:12]
    stem = _safe_slug(video_path.stem, "video")
    return (_transcripts_dir() / f"{stem}_{digest}.md").resolve()


def _segment_time_slug(seconds: float) -> str:
    total = max(0, int(round(float(seconds or 0.0))))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}-{minutes:02d}-{secs:02d}"
    return f"{minutes:02d}-{secs:02d}"


def _segment_file_stem(segment: TranscriptSegment) -> str:
    return (
        f"segment-{int(segment.index):03d}_"
        f"{_segment_time_slug(segment.start_seconds)}_"
        f"{_segment_time_slug(segment.end_seconds)}"
    )


def _append_progress_log(workspace: Path | None, message: str) -> None:
    if workspace is None:
        return
    try:
        log_path = (workspace / "progress.log").resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fp:
            fp.write(str(message).rstrip() + "\n")
    except Exception:
        logger.debug("failed to append progress log", exc_info=True)


def _emit_progress(
    progress: ProgressCallback | None,
    message: str,
    *,
    workspace: Path | None = None,
) -> None:
    safe_message = str(message or "").strip()
    if not safe_message:
        return
    logger.info("video_to_text: %s", safe_message)
    _append_progress_log(workspace, safe_message)
    if progress is not None:
        try:
            progress(safe_message)
        except Exception:
            logger.debug("progress callback failed", exc_info=True)


def _report_locked_audio_strategy(
    transcription_state: AudioTranscriptionState | None,
    *,
    diagnostics: list[str],
    reported_label: str,
    progress: ProgressCallback | None,
    workspace: Path | None,
) -> str:
    current_label = _audio_strategy_label(
        transcription_state.locked_strategy if transcription_state else None
    )
    if not current_label or current_label == reported_label:
        return reported_label
    diagnostics.append(f"locked audio transcription strategy: {current_label}")
    _emit_progress(
        progress,
        f"locked audio transcription strategy: {current_label}",
        workspace=workspace,
    )
    return current_label


def _render_segment_text(segment: TranscriptSegment) -> str:
    lines = [
        f"index: {segment.index}",
        f"start: {_seconds_to_label(segment.start_seconds)}",
        f"end: {_seconds_to_label(segment.end_seconds)}",
        f"status: {segment.status or 'unknown'}",
    ]
    if segment.transcript:
        lines.extend(["", segment.transcript])
    elif segment.error:
        lines.extend(["", f"error: {segment.error}"])
    return "\n".join(lines).strip() + "\n"


def _persist_segment_outputs(
    *,
    audio_dir: Path,
    text_dir: Path,
    segment: TranscriptSegment,
    audio_bytes: bytes | None,
) -> tuple[str, str]:
    stem = _segment_file_stem(segment)
    audio_path = (audio_dir / f"{stem}.mp3").resolve()
    text_path = (text_dir / f"{stem}.txt").resolve()
    if audio_bytes:
        audio_dir.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(bytes(audio_bytes))
    text_dir.mkdir(parents=True, exist_ok=True)
    text_path.write_text(_render_segment_text(segment), encoding="utf-8")
    return str(audio_path if audio_bytes else ""), str(text_path)


async def _read_audio_file_bytes(audio_path: Path) -> bytes:
    return await asyncio.to_thread(audio_path.read_bytes)


def _low_quality_transcript(text: str) -> bool:
    safe_text = str(text or "").strip()
    if not safe_text:
        return True
    lowered = safe_text.lower()
    if safe_text in {"...", "。。。", "…", "……"}:
        return True
    if re.fullmatch(r"[.\s…·。]{2,}", safe_text):
        return True
    bad_markers = (
        "此处为音频中的原话内容",
        "这里是音频中的原话内容",
        "这里应该是音频原话",
        "请提供您需要转写的音频文件或链接",
        "请提供音频文件",
        "upload the audio",
        "provide the audio file",
        "provide the audio content",
    )
    if any(marker in lowered or marker in safe_text for marker in bad_markers):
        return True
    if re.search(r"请提供.{0,20}(音频文件|音频链接)", safe_text):
        return True

    if re.search(r"\b([a-z][a-z0-9'-]{1,})\b(?:\s+\1\b){11,}", lowered):
        return True

    latin_tokens = re.findall(r"\b[a-z][a-z0-9'-]{1,}\b", lowered)
    if len(latin_tokens) >= 40:
        counts: dict[str, int] = {}
        max_count = 0
        max_run = 1
        current_run = 1
        previous = ""
        for token in latin_tokens:
            counts[token] = counts.get(token, 0) + 1
            if counts[token] > max_count:
                max_count = counts[token]
            if token == previous:
                current_run += 1
                if current_run > max_run:
                    max_run = current_run
            else:
                current_run = 1
                previous = token
        if max_run >= 12:
            return True
        if max_count >= 40 and max_count / len(latin_tokens) >= 0.35:
            return True

    return False


def _seconds_to_label(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    total = max(0, int(round(float(seconds))))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


async def _run_subprocess(
    *args: str,
    input_bytes: bytes | None = None,
) -> tuple[int, bytes, bytes]:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if input_bytes is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate(input=input_bytes)
    return int(process.returncode or 0), stdout or b"", stderr or b""


def _parse_ratio(raw: str) -> float | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if "/" in text:
        left, right = text.split("/", 1)
        try:
            denominator = float(right)
            if denominator == 0:
                return None
            return float(left) / denominator
        except Exception:
            return None
    try:
        return float(text)
    except Exception:
        return None


async def probe_video_metadata(video_path: Path) -> VideoMetadata:
    metadata = VideoMetadata(
        mime_type=str(mimetypes.guess_type(str(video_path))[0] or "video/mp4")
    )
    if shutil.which("ffprobe") is None:
        return metadata

    code, stdout, stderr = await _run_subprocess(
        "ffprobe",
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(video_path),
    )
    if code != 0 or not stdout:
        logger.warning(
            "ffprobe failed for %s: %s",
            video_path,
            stderr[:200].decode("utf-8", errors="ignore"),
        )
        return metadata

    try:
        payload = json.loads(stdout.decode("utf-8", errors="ignore"))
    except Exception:
        return metadata

    streams = payload.get("streams") or []
    format_block = payload.get("format") or {}
    try:
        duration = float(format_block.get("duration"))
        if duration > 0:
            metadata.duration_seconds = duration
    except Exception:
        pass

    for stream in streams:
        if not isinstance(stream, dict):
            continue
        codec_type = str(stream.get("codec_type") or "").strip().lower()
        if codec_type == "video" and not metadata.video_codec:
            metadata.video_codec = str(stream.get("codec_name") or "").strip()
            try:
                width = int(stream.get("width") or 0)
                height = int(stream.get("height") or 0)
                metadata.width = width or None
                metadata.height = height or None
            except Exception:
                pass
            metadata.fps = _parse_ratio(
                stream.get("avg_frame_rate") or stream.get("r_frame_rate") or ""
            )
        elif codec_type == "audio" and not metadata.audio_codec:
            metadata.audio_codec = str(stream.get("codec_name") or "").strip()
    return metadata


def _frame_timestamps(duration_seconds: float | None) -> list[float]:
    max_frames = _env_int("VIDEO_TO_TEXT_MAX_FRAMES", 8, 1)
    min_interval = _env_float(
        "VIDEO_TO_TEXT_MIN_FRAME_INTERVAL_SECONDS",
        8.0,
        0.5,
    )
    if duration_seconds is None or duration_seconds <= 0:
        return [0.0]

    interval = max(min_interval, duration_seconds / max_frames)
    current = 0.0
    points: list[float] = []
    while current < duration_seconds and len(points) < max_frames:
        points.append(round(current, 3))
        current += interval
    tail = max(0.0, duration_seconds - 0.5)
    if points and tail > points[-1] + 0.5 and len(points) < max_frames:
        points.append(round(tail, 3))
    return points[:max_frames]


async def extract_frame_samples(
    video_path: Path,
    *,
    duration_seconds: float | None,
    workspace: Path,
) -> tuple[list[FrameSample], list[str]]:
    diagnostics: list[str] = []
    if shutil.which("ffmpeg") is None:
        diagnostics.append("ffmpeg not found, skipped frame extraction")
        return [], diagnostics

    frame_dir = (workspace / "frames").resolve()
    frame_dir.mkdir(parents=True, exist_ok=True)
    frame_max_width = _env_int("VIDEO_TO_TEXT_FRAME_MAX_WIDTH", 960, 240)
    timestamps = _frame_timestamps(duration_seconds)
    frames: list[FrameSample] = []

    for index, timestamp_seconds in enumerate(timestamps, start=1):
        output_path = frame_dir / f"frame-{index:03d}.jpg"
        code, _stdout, stderr = await _run_subprocess(
            "ffmpeg",
            "-y",
            "-ss",
            f"{timestamp_seconds:.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-vf",
            f"scale={frame_max_width}:-2:force_original_aspect_ratio=decrease",
            "-q:v",
            "4",
            str(output_path),
        )
        if code != 0 or not output_path.exists():
            diagnostics.append(
                f"frame {index} extraction failed: "
                f"{stderr[:120].decode('utf-8', errors='ignore').strip() or 'unknown error'}"
            )
            continue
        frames.append(
            FrameSample(
                index=index,
                timestamp_seconds=timestamp_seconds,
                image_path=str(output_path),
            )
        )

    if not frames:
        diagnostics.append("no frames extracted")
    return frames, diagnostics


def _json_block(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    candidates = [raw]
    candidates.extend(re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, flags=re.I))
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", raw):
        try:
            payload, _end = decoder.raw_decode(raw[match.start() :])
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _audio_processing_refusal(raw_text: str) -> bool:
    safe_text = str(raw_text or "").strip().lower()
    refusal_markers = (
        "don't have the capability to access or process audio files",
        "do not have the capability to access or process audio files",
        "can't process audio files",
        "cannot process audio files",
        "please provide the audio content as text",
        "upload the file in a format that can be processed",
        "please provide the audio content for transcription",
    )
    return any(marker in safe_text for marker in refusal_markers)


def _audio_mime_candidates(mime_type: str) -> list[str]:
    raw = str(mime_type or "").strip()
    base = raw.split(";", 1)[0].strip().lower() if raw else ""
    candidates: list[str] = []

    def add(item: str) -> None:
        value = str(item or "").strip()
        if value and value not in candidates:
            candidates.append(value)

    add(raw)
    add(base)

    if base in {"audio/ogg", "audio/opus", "audio/x-opus", "application/ogg"}:
        add("audio/ogg")
        add("audio/ogg; codecs=opus")
        add("audio/opus")
    if base in {"audio/mp3", "audio/mpeg"}:
        add("audio/mpeg")
        add("audio/mp3")

    add("audio/ogg")
    add("audio/ogg; codecs=opus")
    add("audio/webm")
    add("audio/mpeg")
    add("audio/mp4")
    add("audio/wav")
    return candidates


def _audio_base_mime(mime_type: str) -> str:
    return str(mime_type or "").split(";", 1)[0].strip().lower()


def _audio_suffix_for_mime(mime_type: str) -> str:
    base = _audio_base_mime(mime_type)
    mapping = {
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/ogg": ".ogg",
        "audio/opus": ".opus",
        "audio/webm": ".webm",
        "audio/mp4": ".m4a",
        "audio/x-m4a": ".m4a",
        "audio/flac": ".flac",
    }
    guessed = mimetypes.guess_extension(base or "")
    return mapping.get(base, guessed or ".bin")


def _audio_mp3_bitrate_kbps() -> int:
    return _env_int("VIDEO_TO_TEXT_AUDIO_BITRATE_KBPS", 32, 16)


def _mp3_ffmpeg_output_args(*, include_video_flags: bool) -> list[str]:
    args: list[str] = []
    if include_video_flags:
        args.extend(["-vn", "-map", "0:a:0?"])
    args.extend(
        [
            "-ac",
            "1",
            "-ar",
            "16000",
            "-acodec",
            "libmp3lame",
            "-b:a",
            f"{_audio_mp3_bitrate_kbps()}k",
        ]
    )
    return args


async def _extract_audio_segment_to_temp_file(
    input_path: Path,
    *,
    start_seconds: float,
    duration_seconds: float | None,
    suffix: str,
    ffmpeg_output_args: list[str],
) -> tuple[bytes | None, str]:
    with tempfile.TemporaryDirectory(prefix="ikaros-audio-segment-") as temp_dir:
        output_path = (Path(temp_dir) / f"segment{suffix}").resolve()
        args = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-ss",
            f"{max(0.0, start_seconds):.3f}",
        ]
        if duration_seconds is not None and duration_seconds > 0:
            args.extend(["-t", f"{duration_seconds:.3f}"])
        args.extend(ffmpeg_output_args)
        args.append(str(output_path))

        code, _stdout, stderr = await _run_subprocess(*args)
        if code != 0 or not output_path.exists() or output_path.stat().st_size <= 0:
            return (
                None,
                stderr[:200].decode("utf-8", errors="ignore").strip()
                or "audio segment extraction failed",
            )
        return output_path.read_bytes(), ""


def _workspace_audio_track_path(workspace: Path) -> str:
    audio_dir = (workspace / "audio").resolve()
    if not audio_dir.exists():
        return str((audio_dir / "full-audio").resolve())
    preferred = (audio_dir / "full-audio.mp3").resolve()
    if preferred.exists():
        return str(preferred)
    candidates = sorted(audio_dir.glob("full-audio.*"))
    if candidates:
        return str(candidates[0].resolve())
    return str((audio_dir / "full-audio").resolve())


def _sniff_audio_container(audio_bytes: bytes) -> str | None:
    if not audio_bytes:
        return None

    head16 = bytes(audio_bytes[:16])
    head32 = bytes(audio_bytes[:32])

    if head16.startswith(b"OggS"):
        return "ogg"
    if len(head16) >= 12 and head16.startswith(b"RIFF") and head16[8:12] == b"WAVE":
        return "wav"
    if head16.startswith(b"\x1aE\xdf\xa3"):
        return "webm"
    if head16.startswith(b"fLaC"):
        return "flac"
    if head16.startswith(b"ID3"):
        return "mp3"
    if len(head16) >= 2 and head16[0] == 0xFF and (head16[1] & 0xE0) == 0xE0:
        return "mp3"
    if b"ftyp" in head32:
        return "mp4"
    return None


def _ffmpeg_audio_input_format(mime_type: str, audio_bytes: bytes) -> str | None:
    base = _audio_base_mime(mime_type)
    if base in {"audio/ogg", "application/ogg", "audio/opus", "audio/x-opus"}:
        return "ogg"
    if base == "audio/webm":
        return "webm"
    if base in {"audio/mp4", "audio/x-m4a"}:
        return "mp4"
    if base in {"audio/aac", "audio/x-aac"}:
        return "aac"
    if "mpeg" in base or "mp3" in base:
        return "mp3"
    if "wav" in base:
        return "wav"
    if "flac" in base:
        return "flac"

    sniffed = _sniff_audio_container(audio_bytes)
    if sniffed in {"ogg", "webm", "mp4", "aac", "mp3", "wav", "flac"}:
        return sniffed
    return None


def _should_try_wav_transcode(mime_type: str, audio_bytes: bytes) -> bool:
    base = _audio_base_mime(mime_type)
    if "wav" in base:
        return False
    if base in {
        "audio/ogg",
        "application/ogg",
        "audio/opus",
        "audio/x-opus",
        "audio/webm",
        "audio/mp4",
        "audio/x-m4a",
        "audio/aac",
        "audio/x-aac",
        "audio/flac",
        "audio/mpeg",
        "audio/mp3",
    }:
        return True
    return _sniff_audio_container(audio_bytes) in {"ogg", "webm", "mp4", "flac", "mp3"}


async def _transcode_audio_bytes_to_wav(
    audio_bytes: bytes,
    mime_type: str,
) -> bytes | None:
    if not audio_bytes or not _should_try_wav_transcode(mime_type, audio_bytes):
        return None
    if shutil.which("ffmpeg") is None:
        return None

    args = ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error"]
    input_fmt = _ffmpeg_audio_input_format(mime_type, audio_bytes)
    if input_fmt:
        args.extend(["-f", input_fmt])
    args.extend(["-i", "pipe:0", "-c:a", "pcm_s16le", "-f", "wav", "pipe:1"])

    code, stdout, _stderr = await _run_subprocess(*args, input_bytes=bytes(audio_bytes))
    if code != 0 or not stdout:
        return None
    return stdout


async def _transcode_audio_bytes_to_mp3(
    audio_bytes: bytes,
    mime_type: str,
) -> bytes | None:
    if not audio_bytes:
        return None
    base = _audio_base_mime(mime_type)
    if base in {"audio/mpeg", "audio/mp3"}:
        return bytes(audio_bytes)
    if shutil.which("ffmpeg") is None:
        return None

    args = ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error"]
    input_fmt = _ffmpeg_audio_input_format(mime_type, audio_bytes)
    if input_fmt:
        args.extend(["-f", input_fmt])
    args.extend(
        [
            "-i",
            "pipe:0",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-acodec",
            "libmp3lame",
            "-b:a",
            f"{_audio_mp3_bitrate_kbps()}k",
            "-f",
            "mp3",
            "pipe:1",
        ]
    )

    code, stdout, _stderr = await _run_subprocess(*args, input_bytes=bytes(audio_bytes))
    if code != 0 or not stdout:
        return None
    return stdout


async def _build_audio_transcription_attempts(
    audio_bytes: bytes,
    mime_type: str,
) -> list[tuple[str, bytes, str]]:
    attempts: list[tuple[str, bytes, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(candidate_mime: str, candidate_bytes: bytes, source_kind: str) -> None:
        key = (str(candidate_mime or "").strip(), str(source_kind or "").strip())
        if not key[0] or key in seen:
            return
        seen.add(key)
        attempts.append((key[0], candidate_bytes, key[1]))

    transcoded_wav = await _transcode_audio_bytes_to_wav(audio_bytes, mime_type)
    if transcoded_wav and not _audio_request_too_large(len(transcoded_wav)):
        add("audio/wav", transcoded_wav, "transcoded_wav")
    for candidate_mime in _audio_mime_candidates(mime_type):
        add(candidate_mime, audio_bytes, "raw")
    return attempts


async def describe_frame(
    frame: FrameSample,
    *,
    vision_model: str,
    client: Any,
) -> FrameSample:
    image_bytes = Path(frame.image_path).read_bytes()
    prompt = (
        "请读取这张视频关键帧并返回 JSON："
        '{"description":"客观描述画面中的人物、动作、场景","visible_text":"画面中能读到的文字，没有则空字符串"}。'
        "不要总结整段视频，只描述这一帧。"
    )
    contents = [
        {
            "role": "user",
            "parts": [
                {"text": prompt},
                {
                    "inline_data": {
                        "mime_type": frame.mime_type,
                        "data": base64.b64encode(image_bytes).decode("utf-8"),
                    }
                },
            ],
        }
    ]
    try:
        raw = await generate_text(
            async_client=client,
            model=vision_model,
            contents=contents,
            config={"response_mime_type": "application/json"},
        )
        payload = _json_block(raw)
        description = str(payload.get("description") or "").strip()
        visible_text = str(payload.get("visible_text") or "").strip()
        if not description and raw.strip():
            description = raw.strip()
        frame.description = description
        frame.visible_text = visible_text
    except Exception as exc:
        frame.error = str(exc)
    return frame


async def enrich_frames(frames: list[FrameSample]) -> tuple[list[FrameSample], list[str]]:
    diagnostics: list[str] = []
    if not frames:
        return frames, diagnostics

    vision_model = select_model_for_role("vision") or select_model_for_role("primary")
    client = get_client_for_model(vision_model, is_async=True)
    if client is None:
        diagnostics.append("vision client unavailable, skipped frame description")
        return frames, diagnostics

    enriched: list[FrameSample] = []
    for frame in frames:
        enriched_frame = await describe_frame(frame, vision_model=vision_model, client=client)
        if enriched_frame.error:
            diagnostics.append(
                f"frame {frame.index} description failed: {enriched_frame.error}"
            )
        enriched.append(enriched_frame)
    return enriched, diagnostics


def _normalize_transcribed_text(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    if not text:
        return ""
    for wrapped in ("```json", "```"):
        if text.lower().startswith(wrapped):
            text = text[len(wrapped) :].strip()
    if text.endswith("```"):
        text = text[:-3].strip()
    if text.lower().startswith("json"):
        text = text[4:].strip()
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                for key in ("transcript", "text", "content", "result"):
                    value = parsed.get(key)
                    if isinstance(value, str) and value.strip():
                        text = value.strip()
                        break
        except Exception:
            pass
    for prefix in ("转写：", "转写结果：", "识别结果：", "文本："):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
    return text


def _parse_audio_response_payload(raw_text: str) -> tuple[str, str]:
    text = str(raw_text or "").strip()
    if not text:
        return "empty", ""
    payload = _json_block(text)
    if payload:
        error_text = _normalize_transcribed_text(str(payload.get("error") or ""))
        if error_text:
            return "failed", error_text
        status = str(payload.get("status") or "").strip().lower() or "transcribed"
        transcript = _normalize_transcribed_text(
            str(
                payload.get("transcript")
                or payload.get("text")
                or payload.get("content")
                or ""
            )
        )
        return status, transcript
    return "unstructured", _normalize_transcribed_text(text)


async def extract_audio_segment(
    video_path: Path,
    *,
    start_seconds: float,
    duration_seconds: float | None,
) -> tuple[bytes | None, str, str]:
    if shutil.which("ffmpeg") is None:
        return None, "ffmpeg not found", ""
    payload, error = await _extract_audio_segment_to_temp_file(
        video_path,
        start_seconds=start_seconds,
        duration_seconds=duration_seconds,
        suffix=".mp3",
        ffmpeg_output_args=_mp3_ffmpeg_output_args(include_video_flags=True),
    )
    if payload:
        return payload, "", "audio/mpeg"
    return None, error or "audio extraction failed", ""


async def extract_audio_track_file(
    video_path: Path,
    *,
    workspace: Path,
) -> tuple[Path | None, str, str]:
    if shutil.which("ffmpeg") is None:
        return None, "ffmpeg not found", ""

    audio_dir = (workspace / "audio").resolve()
    audio_dir.mkdir(parents=True, exist_ok=True)
    output_path = (audio_dir / "full-audio.mp3").resolve()
    args = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        *_mp3_ffmpeg_output_args(include_video_flags=True),
        str(output_path),
    ]
    code, _stdout, stderr = await _run_subprocess(*args)
    if code != 0 or not output_path.exists() or output_path.stat().st_size <= 0:
        return (
            None,
            stderr[:200].decode("utf-8", errors="ignore").strip()
            or "audio track extraction failed",
            "",
        )
    return output_path, "", "audio/mpeg"


async def extract_audio_file_segment(
    audio_path: Path,
    *,
    start_seconds: float,
    duration_seconds: float | None,
    mime_type: str = "audio/mpeg",
) -> tuple[bytes | None, str, str]:
    if shutil.which("ffmpeg") is None:
        return None, "ffmpeg not found", ""
    payload, error = await _extract_audio_segment_to_temp_file(
        audio_path,
        start_seconds=start_seconds,
        duration_seconds=duration_seconds,
        suffix=".mp3",
        ffmpeg_output_args=_mp3_ffmpeg_output_args(include_video_flags=False),
    )
    if payload:
        return payload, "", "audio/mpeg"
    return None, error or "audio segment extraction failed", ""


async def transcribe_audio_bytes(audio_bytes: bytes, mime_type: str) -> tuple[str, str]:
    status, detail, _strategy = await _transcribe_audio_bytes_internal(
        audio_bytes,
        mime_type,
        transcription_state=None,
    )
    return status, detail


async def _transcribe_audio_bytes_internal(
    audio_bytes: bytes,
    mime_type: str,
    *,
    transcription_state: AudioTranscriptionState | None,
) -> tuple[str, str, AudioTranscriptionStrategy | None]:
    locked_strategy = transcription_state.locked_strategy if transcription_state else None
    if locked_strategy is not None:
        if locked_strategy.model == _WHISPER_HTTP_MODEL:
            status, detail, strategy = await _transcribe_audio_bytes_with_whisper_http(
                audio_bytes,
                mime_type,
            )
            if status not in {"failed", "unsupported_modality"}:
                if (
                    transcription_state is not None
                    and strategy is not None
                    and _status_locks_audio_strategy(status)
                ):
                    transcription_state.locked_strategy = strategy
                return status, detail, strategy or locked_strategy
            if transcription_state is not None:
                transcription_state.locked_strategy = None
        elif transcription_state is not None:
            transcription_state.locked_strategy = None

    if not _whisper_http_enabled():
        return "failed", "whisper http endpoint not configured", None

    status, detail, strategy = await _transcribe_audio_bytes_with_whisper_http(
        audio_bytes,
        mime_type,
    )
    if status not in {"failed", "unsupported_modality"}:
        if (
            transcription_state is not None
            and strategy is not None
            and _status_locks_audio_strategy(status)
        ):
            transcription_state.locked_strategy = strategy
        return status, detail, strategy
    return "failed", detail or "whisper http transcription failed", None


async def _transcribe_audio_bytes_with_target(
    voice_model: str,
    client: Any,
    audio_bytes: bytes,
    mime_type: str,
    *,
    attempts: list[tuple[str, bytes, str]] | None = None,
    audio_part_styles: list[str] | None = None,
    strategy_filter: AudioTranscriptionStrategy | None = None,
) -> tuple[str, str, AudioTranscriptionStrategy | None]:
    prompt = (
        "请将这段音频转写为文字。"
        "返回 JSON，格式为 "
        '{"status":"transcribed|no_audio|unintelligible","transcript":"..."}。'
        "如果成功识别，transcript 只保留音频原话。"
    )
    candidate_attempts = attempts or await _build_audio_transcription_attempts(
        audio_bytes,
        mime_type,
    )
    if strategy_filter is not None:
        candidate_attempts = [
            (candidate_mime, candidate_bytes, source)
            for candidate_mime, candidate_bytes, source in candidate_attempts
            if candidate_mime == strategy_filter.mime_type
            and source == strategy_filter.source_kind
        ]
        if not candidate_attempts:
            return "failed", "locked audio strategy could not prepare matching payload", None

    last_invalid_error = ""
    last_error = ""
    last_status = "failed"
    unsupported_detail = ""
    last_lockable_strategy: AudioTranscriptionStrategy | None = None
    for candidate_mime, candidate_bytes, source in candidate_attempts:
        contents = [
            {
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": candidate_mime,
                            "data": base64.b64encode(candidate_bytes).decode("utf-8"),
                        }
                    },
                ],
            }
        ]

        candidate_invalid_error = ""
        candidate_response_handled = False
        for audio_part_style in (audio_part_styles or _candidate_audio_part_styles()):
            strategy = AudioTranscriptionStrategy(
                model=voice_model,
                audio_part_style=audio_part_style,
                mime_type=candidate_mime,
                source_kind=source,
            )
            try:
                raw = await _generate_audio_transcription_text(
                    async_client=client,
                    model=voice_model,
                    contents=contents,
                    config={
                        "response_mime_type": "application/json",
                        "audio_part_style": audio_part_style,
                    },
                )
            except Exception as exc:
                error_text = str(exc)
                if _invalid_audio_part_type_error(
                    error_text
                ) or _audio_part_style_parameter_error(error_text):
                    candidate_invalid_error = error_text
                    last_invalid_error = error_text
                    continue
                try:
                    raw = await _generate_audio_transcription_text(
                        async_client=client,
                        model=voice_model,
                        contents=contents,
                        config={
                            "audio_part_style": audio_part_style,
                        },
                    )
                except Exception as inner_exc:
                    error_text = str(inner_exc)
                    if _invalid_audio_part_type_error(
                        error_text
                    ) or _audio_part_style_parameter_error(error_text):
                        candidate_invalid_error = error_text
                        last_invalid_error = error_text
                        continue
                    last_error = error_text
                    break

            status, transcript = _parse_audio_response_payload(raw)
            if status == "unstructured" and _audio_processing_refusal(raw):
                unsupported_detail = raw
                candidate_response_handled = True
                break
            if status == "unstructured":
                if _low_quality_transcript(transcript):
                    last_status = "empty"
                    candidate_response_handled = True
                    break
                return "transcribed", transcript, strategy
            if status == "transcribed" and _low_quality_transcript(transcript):
                last_status = "empty"
                candidate_response_handled = True
                break
            if status in {"no_audio", "unintelligible", "empty"}:
                last_status = status
                if _status_locks_audio_strategy(status):
                    last_lockable_strategy = strategy
                candidate_response_handled = True
                break
            return status, transcript, strategy

        if candidate_response_handled:
            continue
        if candidate_invalid_error:
            if source == "transcoded_wav":
                continue
            return "unsupported_modality", candidate_invalid_error, None

    if unsupported_detail or last_invalid_error:
        return "unsupported_modality", unsupported_detail or last_invalid_error, None
    if last_status in {"no_audio", "unintelligible", "empty"}:
        return last_status, "", last_lockable_strategy
    if last_error:
        return "failed", last_error, None
    return "failed", "audio transcription failed", None


async def _generate_audio_transcription_text(
    *,
    async_client: Any,
    model: str,
    contents: Any,
    config: dict[str, Any],
) -> str:
    timeout_seconds = _audio_request_timeout_seconds()
    try:
        return await asyncio.wait_for(
            generate_text(
                async_client=async_client,
                model=model,
                contents=contents,
                config=config,
            ),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        raise RuntimeError(
            f"audio transcription request timed out after {timeout_seconds:.1f}s"
        ) from exc


async def _transcribe_audio_bytes_with_whisper_http(
    audio_bytes: bytes,
    mime_type: str,
) -> tuple[str, str, AudioTranscriptionStrategy | None]:
    endpoint = _whisper_http_endpoint()
    if not endpoint:
        return "failed", "whisper http endpoint not configured", None

    payload_bytes = bytes(audio_bytes)
    safe_mime_type = _audio_base_mime(mime_type) or "application/octet-stream"
    transcoded_mp3 = await _transcode_audio_bytes_to_mp3(audio_bytes, mime_type)
    if transcoded_mp3:
        payload_bytes = transcoded_mp3
        safe_mime_type = "audio/mpeg"
    suffix = _audio_suffix_for_mime(safe_mime_type)
    strategy = AudioTranscriptionStrategy(
        model=_WHISPER_HTTP_MODEL,
        audio_part_style="multipart_form",
        mime_type=safe_mime_type,
        source_kind="http_file",
    )
    data = {
        "response_format": _whisper_http_response_format(),
        "temperature": f"{_whisper_http_temperature():.2f}",
        "temperature_inc": f"{_whisper_http_temperature_inc():.2f}",
    }
    language = _whisper_http_language()
    if language:
        data["language"] = language
    if _whisper_http_no_timestamps():
        data["no_timestamps"] = "true"

    timeout_seconds = _whisper_http_timeout_seconds()
    timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10.0))
    files = {"file": (f"audio{suffix}", payload_bytes, safe_mime_type)}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(endpoint, data=data, files=files)
            response.raise_for_status()
    except httpx.TimeoutException as exc:
        return "failed", f"whisper http request timed out after {timeout_seconds:.1f}s", None
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:400].strip()
        message = f"whisper http error {exc.response.status_code}"
        if body:
            message += f": {body}"
        return "failed", message, None
    except httpx.HTTPError as exc:
        return "failed", f"whisper http request failed: {exc}", None

    raw_text = response.text.strip()
    if not raw_text:
        return "empty", "", strategy

    status, transcript = _parse_audio_response_payload(raw_text)
    if status == "unstructured":
        normalized = _normalize_transcribed_text(raw_text)
        if _low_quality_transcript(normalized):
            return "empty", "", strategy
        return "transcribed", normalized, strategy
    if status == "transcribed":
        if _low_quality_transcript(transcript):
            return "empty", "", strategy
        return "transcribed", transcript, strategy
    if status in {"no_audio", "unintelligible", "empty"}:
        return status, "", strategy
    if transcript:
        return status, transcript, strategy
    return "failed", raw_text[:400], None


async def _probe_audio_transcription_mode(
    audio_path: Path,
    *,
    duration_seconds: float | None,
    mime_type: str = "audio/mpeg",
    transcription_state: AudioTranscriptionState | None = None,
    progress: ProgressCallback | None = None,
    workspace: Path | None = None,
) -> tuple[bool, list[str]]:
    diagnostics: list[str] = []
    whisper_endpoint = _whisper_http_endpoint()
    if whisper_endpoint:
        diagnostics.append(f"whisper http endpoint configured: {whisper_endpoint}")
        _emit_progress(
            progress,
            f"whisper http endpoint configured: {whisper_endpoint}",
            workspace=workspace,
        )
    if not whisper_endpoint:
        diagnostics.append("whisper http endpoint not configured")
        diagnostics.append(_fatal_audio_diagnostic("whisper http endpoint not configured"))
        return False, diagnostics

    probe_seconds = _env_float("VIDEO_TO_TEXT_AUDIO_PROBE_SECONDS", 20.0, 5.0)
    if duration_seconds is not None and duration_seconds > 0:
        probe_seconds = min(probe_seconds, duration_seconds)
    _emit_progress(
        progress,
        f"audio probe: extracting first {probe_seconds:.1f}s from audio track",
        workspace=workspace,
    )
    audio_bytes, error, extracted_mime_type = await extract_audio_file_segment(
        audio_path,
        start_seconds=0.0,
        duration_seconds=probe_seconds,
        mime_type=mime_type,
    )
    if not audio_bytes:
        if error:
            diagnostics.append(f"audio probe extraction failed: {error}")
        return True, diagnostics

    _emit_progress(
        progress,
        f"audio probe: transcribing {len(audio_bytes)} bytes",
        workspace=workspace,
    )
    status, detail, _strategy = await _transcribe_audio_bytes_internal(
        audio_bytes,
        extracted_mime_type or mime_type or "audio/mpeg",
        transcription_state=transcription_state,
    )
    if status == "unsupported_modality":
        diagnostics.append(
            "audio input unsupported on current backend, video fallback disabled"
        )
        diagnostics.append(detail)
        diagnostics.append(
            _fatal_audio_diagnostic(detail or "audio input unsupported on current backend")
        )
        return False, diagnostics
    if status == "failed":
        diagnostics.append("audio probe failed with fatal backend error")
        if detail:
            diagnostics.append(detail)
        diagnostics.append(
            _fatal_audio_diagnostic(detail or "audio probe failed with fatal backend error")
        )
        return False, diagnostics
    if status == "no_audio":
        diagnostics.append("audio probe returned no_audio, keep audio transcription path")
        if detail:
            diagnostics.append(detail)
        return True, diagnostics
    return True, diagnostics


async def transcribe_audio_segments(
    video_path: Path,
    *,
    duration_seconds: float | None,
    workspace: Path | None = None,
    progress: ProgressCallback | None = None,
) -> tuple[list[TranscriptSegment], list[str], bool]:
    created_workspace = False
    active_workspace = workspace
    if active_workspace is None:
        active_workspace = Path(
            tempfile.mkdtemp(prefix="ikaros-video-to-text-audio-", dir=str(_downloads_root()))
        ).resolve()
        created_workspace = True

    try:
        audio_path, audio_error, audio_mime_type = await extract_audio_track_file(
            video_path,
            workspace=active_workspace,
        )
        if audio_path is None:
            diagnostics = []
            if audio_error:
                diagnostics.append(f"audio track extraction failed: {audio_error}")
                diagnostics.append(_fatal_audio_diagnostic(audio_error))
            return [], diagnostics, True

        _emit_progress(
            progress,
            f"audio track extracted: {audio_path}",
            workspace=active_workspace,
        )
        transcription_state = AudioTranscriptionState()
        reported_locked_strategy = ""
        diagnostics: list[str] = []
        whisper_endpoint = _whisper_http_endpoint()
        if not whisper_endpoint:
            diagnostics.append("whisper http endpoint not configured")
            diagnostics.append(_fatal_audio_diagnostic("whisper http endpoint not configured"))
            return [], diagnostics, True
        diagnostics.append(f"whisper http endpoint configured: {whisper_endpoint}")
        _emit_progress(
            progress,
            f"whisper http endpoint configured: {whisper_endpoint}",
            workspace=active_workspace,
        )
        reported_locked_strategy = _report_locked_audio_strategy(
            transcription_state,
            diagnostics=diagnostics,
            reported_label=reported_locked_strategy,
            progress=progress,
            workspace=active_workspace,
        )
        diagnostics.append(f"audio track extracted: {audio_path}")
        segment_audio_dir = (active_workspace / "audio" / "segments").resolve()
        segment_text_dir = (active_workspace / "audio" / "transcripts").resolve()
        diagnostics.append(f"segment audio dir: {segment_audio_dir}")
        diagnostics.append(f"segment text dir: {segment_text_dir}")
        segments: list[TranscriptSegment] = []
        audio_incomplete = False
        segment_seconds = _env_float("VIDEO_TO_TEXT_AUDIO_SEGMENT_SECONDS", 600.0, 15.0)
        minimum_segment_seconds = _env_float(
            "VIDEO_TO_TEXT_MIN_AUDIO_SEGMENT_SECONDS",
            30.0,
            5.0,
        )

        if duration_seconds is None or duration_seconds <= 0:
            audio_incomplete = True
            diagnostics.append(
                "video duration unknown, only first audio segment will be transcribed"
            )

        if _whisper_http_enabled() and _whisper_http_prefer_full_audio():
            max_full_audio_seconds = _whisper_http_max_full_audio_seconds()
            if duration_seconds is not None and duration_seconds > max_full_audio_seconds:
                diagnostics.append(
                    "whisper full-audio mode skipped: "
                    f"duration {duration_seconds:.1f}s exceeds "
                    f"{max_full_audio_seconds:.1f}s limit"
                )
                _emit_progress(
                    progress,
                    "full audio transcription skipped: video too long, using segments",
                    workspace=active_workspace,
                )
            else:
                full_audio_bytes = await _read_audio_file_bytes(audio_path)
                full_audio_size = len(full_audio_bytes)
                full_audio_segment = TranscriptSegment(
                    index=1,
                    start_seconds=0.0,
                    end_seconds=max(float(duration_seconds or 0.0), 0.0),
                )
                diagnostics.append("whisper full-audio mode enabled")
                diagnostics.append(f"full audio bytes: {full_audio_size}")
                _emit_progress(
                    progress,
                    f"full audio transcription: sending {full_audio_size} bytes",
                    workspace=active_workspace,
                )
                status, transcript_or_error, _strategy = await _transcribe_audio_bytes_internal(
                    full_audio_bytes,
                    audio_mime_type or "audio/mpeg",
                    transcription_state=transcription_state,
                )
                reported_locked_strategy = _report_locked_audio_strategy(
                    transcription_state,
                    diagnostics=diagnostics,
                    reported_label=reported_locked_strategy,
                    progress=progress,
                    workspace=active_workspace,
                )
                if status == "transcribed":
                    full_audio_segment.status = "transcribed"
                    full_audio_segment.transcript = transcript_or_error
                    _persist_segment_outputs(
                        audio_dir=segment_audio_dir,
                        text_dir=segment_text_dir,
                        segment=full_audio_segment,
                        audio_bytes=full_audio_bytes,
                    )
                    diagnostics.append("full audio transcription completed without segmentation")
                    _emit_progress(
                        progress,
                        "full audio transcription: transcribed",
                        workspace=active_workspace,
                    )
                    return [full_audio_segment], diagnostics, audio_incomplete

                full_audio_segment.status = "failed"
                full_audio_segment.error = (
                    transcript_or_error or status or "audio transcription failed"
                )
                _persist_segment_outputs(
                    audio_dir=segment_audio_dir,
                    text_dir=segment_text_dir,
                    segment=full_audio_segment,
                    audio_bytes=full_audio_bytes,
                )
                diagnostics.append(
                    "full audio transcription failed, falling back to segmented transcription"
                )
                if transcript_or_error:
                    diagnostics.append(
                        f"full audio transcription detail: {transcript_or_error}"
                    )
                _emit_progress(
                    progress,
                    f"full audio transcription: {status or 'failed'}, fallback to segments",
                    workspace=active_workspace,
                )

        start_seconds = 0.0
        index = 1
        while True:
            if duration_seconds is not None and duration_seconds > 0:
                remaining_seconds = max(0.0, duration_seconds - start_seconds)
                if remaining_seconds <= 0:
                    break
                clip_seconds = min(segment_seconds, remaining_seconds)
            else:
                if index > 1:
                    break
                clip_seconds = segment_seconds

            current_clip_seconds = clip_seconds
            while True:
                if current_clip_seconds <= 0:
                    break

                audio_bytes, error, extracted_mime_type = await extract_audio_file_segment(
                    audio_path,
                    start_seconds=start_seconds,
                    duration_seconds=current_clip_seconds,
                    mime_type=audio_mime_type or "audio/mpeg",
                )
                segment = TranscriptSegment(
                    index=index,
                    start_seconds=start_seconds,
                    end_seconds=start_seconds + current_clip_seconds,
                )
                _emit_progress(
                    progress,
                    "segment "
                    f"{index}: extracting {_seconds_to_label(start_seconds)} -> "
                    f"{_seconds_to_label(start_seconds + current_clip_seconds)}",
                    workspace=active_workspace,
                )
                if not audio_bytes:
                    segment.status = "failed"
                    segment.error = error or "audio extraction failed"
                    _persist_segment_outputs(
                        audio_dir=segment_audio_dir,
                        text_dir=segment_text_dir,
                        segment=segment,
                        audio_bytes=None,
                    )
                    diagnostics.append(
                        f"audio segment {index} extraction failed: {segment.error}"
                    )
                    segments.append(segment)
                    start_seconds += current_clip_seconds
                    index += 1
                    break

                if _audio_request_too_large(len(audio_bytes)):
                    smaller_clip_seconds = _next_smaller_segment_seconds(
                        current_clip_seconds,
                        minimum_seconds=minimum_segment_seconds,
                    )
                    if smaller_clip_seconds is not None:
                        diagnostics.append(
                            "audio segment auto-shrunk before transcription "
                            f"at {_seconds_to_label(start_seconds)}: "
                            f"{current_clip_seconds:.1f}s -> {smaller_clip_seconds:.1f}s"
                        )
                        current_clip_seconds = smaller_clip_seconds
                    continue

                _emit_progress(
                    progress,
                    f"segment {index}: transcribing {len(audio_bytes)} bytes",
                    workspace=active_workspace,
                )
                status, transcript_or_error, _strategy = await _transcribe_audio_bytes_internal(
                    audio_bytes,
                    extracted_mime_type or audio_mime_type or "audio/mpeg",
                    transcription_state=transcription_state,
                )
                reported_locked_strategy = _report_locked_audio_strategy(
                    transcription_state,
                    diagnostics=diagnostics,
                    reported_label=reported_locked_strategy,
                    progress=progress,
                    workspace=active_workspace,
                )
                if status == "failed" and _request_body_too_large_error(
                    transcript_or_error
                ):
                    smaller_clip_seconds = _next_smaller_segment_seconds(
                        current_clip_seconds,
                        minimum_seconds=minimum_segment_seconds,
                    )
                    if smaller_clip_seconds is not None:
                        diagnostics.append(
                            "audio segment auto-shrunk after oversized request "
                            f"at {_seconds_to_label(start_seconds)}: "
                            f"{current_clip_seconds:.1f}s -> {smaller_clip_seconds:.1f}s"
                        )
                        current_clip_seconds = smaller_clip_seconds
                        continue
                if status == "unsupported_modality":
                    segment.status = "failed"
                    segment.error = transcript_or_error or "audio input unsupported"
                    _persist_segment_outputs(
                        audio_dir=segment_audio_dir,
                        text_dir=segment_text_dir,
                        segment=segment,
                        audio_bytes=audio_bytes,
                    )
                    diagnostics.append(
                        "audio input unsupported during segmented transcription, "
                        "stopped without video fallback"
                    )
                    diagnostics.append(segment.error)
                    diagnostics.append(_fatal_audio_diagnostic(segment.error))
                    segments.append(segment)
                    _emit_progress(
                        progress,
                        f"segment {index}: failed unsupported_modality - {segment.error}",
                        workspace=active_workspace,
                    )
                    return segments, diagnostics, True
                if status == "failed":
                    segment.status = "failed"
                    segment.error = transcript_or_error or "audio transcription failed"
                    _persist_segment_outputs(
                        audio_dir=segment_audio_dir,
                        text_dir=segment_text_dir,
                        segment=segment,
                        audio_bytes=audio_bytes,
                    )
                    diagnostics.append(
                        "audio segmented transcription aborted after fatal backend error"
                    )
                    diagnostics.append(segment.error)
                    diagnostics.append(_fatal_audio_diagnostic(segment.error))
                    segments.append(segment)
                    _emit_progress(
                        progress,
                        f"segment {index}: failed - {segment.error}",
                        workspace=active_workspace,
                    )
                    return segments, diagnostics, True

                segment.status = status
                if status == "transcribed":
                    segment.transcript = transcript_or_error
                elif status in {"no_audio", "unintelligible", "empty"}:
                    segment.error = status
                else:
                    segment.error = transcript_or_error
                    diagnostics.append(
                        f"audio segment {index} transcription failed: {segment.error}"
                    )
                _persist_segment_outputs(
                    audio_dir=segment_audio_dir,
                    text_dir=segment_text_dir,
                    segment=segment,
                    audio_bytes=audio_bytes,
                )
                _emit_progress(
                    progress,
                    f"segment {index}: {segment.status or 'unknown'}",
                    workspace=active_workspace,
                )
                segments.append(segment)
                start_seconds += current_clip_seconds
                index += 1
                break

            if duration_seconds is None or duration_seconds <= 0:
                break
        return segments, diagnostics, audio_incomplete
    finally:
        if created_workspace and active_workspace is not None:
            shutil.rmtree(active_workspace, ignore_errors=True)


def render_markdown_artifact(
    *,
    video_path: Path,
    metadata: VideoMetadata,
    frames: list[FrameSample],
    transcripts: list[TranscriptSegment],
    diagnostics: list[str],
) -> str:
    lines = [
        "# 视频文本工件",
        "",
        "## 元数据",
        f"- 源视频：`{video_path}`",
        f"- MIME：`{metadata.mime_type or 'video/mp4'}`",
        f"- 时长：`{_seconds_to_label(metadata.duration_seconds)}`",
        f"- 分辨率：`{metadata.width or '?'} x {metadata.height or '?'}`",
        f"- 帧率：`{metadata.fps or '?'}`",
        f"- 视频编码：`{metadata.video_codec or '?'}`",
        f"- 音频编码：`{metadata.audio_codec or '?'}`",
        f"- 抽帧数：`{len(frames)}`",
        f"- 音轨分段数：`{len(transcripts)}`",
        "",
    ]

    if diagnostics:
        lines.extend(["## 诊断", ""])
        for item in diagnostics:
            lines.append(f"- {item}")
        lines.append("")

    lines.extend(["## 视觉时间轴", ""])
    if not frames:
        lines.append("- 未成功提取可用关键帧。")
        lines.append("")
    else:
        for frame in frames:
            lines.extend(
                [
                    f"### [{_seconds_to_label(frame.timestamp_seconds)}] 帧 {frame.index}",
                    f"- 画面描述：{frame.description or '未提取'}",
                    f"- OCR：{frame.visible_text or '无'}",
                ]
            )
            if frame.error:
                lines.append(f"- 错误：{frame.error}")
            lines.append("")

    lines.extend(["## 音轨转写", ""])
    if not transcripts:
        lines.append("- 未成功提取可用音轨。")
        lines.append("")
    else:
        for segment in transcripts:
            lines.extend(
                [
                    f"### [{_seconds_to_label(segment.start_seconds)} - {_seconds_to_label(segment.end_seconds)}]",
                    f"- 状态：`{segment.status or 'unknown'}`",
                ]
            )
            if segment.transcript:
                lines.append("")
                lines.append(segment.transcript)
            if segment.error:
                lines.append(f"- 备注：{segment.error}")
            lines.append("")

    return "\n".join(lines).strip() + "\n"


def _result_from_cache(record: dict[str, Any], *, platform: str, file_id: str) -> VideoTextResult:
    metadata = VideoMetadata(
        duration_seconds=record.get("duration_seconds"),
        mime_type=str(record.get("mime_type") or ""),
    )
    return VideoTextResult(
        ok=bool(record.get("artifact_path")),
        artifact_path=str(record.get("artifact_path") or ""),
        source_video_path=str(record.get("source_video_path") or ""),
        mime_type=str(record.get("mime_type") or ""),
        workspace_path=str(record.get("workspace_path") or ""),
        audio_track_path=str(record.get("audio_track_path") or ""),
        segment_audio_dir=str(record.get("segment_audio_dir") or ""),
        segment_text_dir=str(record.get("segment_text_dir") or ""),
        progress_log_path=str(record.get("progress_log_path") or ""),
        metadata=metadata,
        frame_count=int(record.get("frame_count") or 0),
        transcript_segment_count=int(record.get("transcript_segment_count") or 0),
        diagnostics=[
            str(item).strip()
            for item in list(record.get("diagnostics") or [])
            if str(item).strip()
        ],
        from_cache=True,
        cached_file_id=str(file_id or ""),
        cached_platform=str(platform or ""),
    )


async def localize_video_message(
    ctx: UnifiedContext,
    *,
    file_id: str,
    mime_type: str,
    file_name: str = "",
) -> Path:
    content = bytes(await ctx.dowikarosd_file(file_id))
    if not content:
        raise ValueError("empty video payload")
    digest = hashlib.sha1(
        f"{ctx.message.platform}:{file_id}:{len(content)}".encode("utf-8")
    ).hexdigest()[:12]
    suffix = _video_suffix_from_mime(mime_type)
    stem = _safe_slug(Path(file_name).stem, f"video_{digest}")
    output_path = (_video_inputs_dir() / f"{stem}_{digest}{suffix}").resolve()
    if not output_path.exists():
        output_path.write_bytes(content)
    return output_path


async def ensure_video_artifact_for_path(
    path: str | Path,
    *,
    file_id: str = "",
    platform: str = "",
    mime_type: str = "",
    progress: ProgressCallback | None = None,
) -> VideoTextResult:
    video_path = Path(str(path or "")).expanduser()
    if not video_path.is_absolute():
        video_path = video_path.resolve()
    if not video_path.exists() or not video_path.is_file():
        return VideoTextResult(
            ok=False,
            diagnostics=[f"video file not found: {video_path}"],
        )

    artifact_path = _artifact_path_for_video(video_path)
    if artifact_path.exists():
        result = VideoTextResult(
            ok=True,
            artifact_path=str(artifact_path),
            source_video_path=str(video_path),
            mime_type=str(mime_type or mimetypes.guess_type(str(video_path))[0] or "video/mp4"),
            metadata=await probe_video_metadata(video_path),
            diagnostics=[],
        )
        if file_id and platform:
            cached = await get_cached_artifact(platform, file_id)
            if cached is None:
                await save_cached_artifact(
                    platform,
                    file_id,
                    artifact_path=str(artifact_path),
                    source_video_path=str(video_path),
                    mime_type=result.mime_type,
                    workspace_path=result.workspace_path,
                    audio_track_path=result.audio_track_path,
                    segment_audio_dir=result.segment_audio_dir,
                    segment_text_dir=result.segment_text_dir,
                    progress_log_path=result.progress_log_path,
                    duration_seconds=result.metadata.duration_seconds,
                    frame_count=result.frame_count,
                    transcript_segment_count=result.transcript_segment_count,
                    diagnostics=result.diagnostics,
                )
        return result

    workspace = Path(
        tempfile.mkdtemp(prefix="ikaros-video-to-text-", dir=str(_downloads_root()))
    ).resolve()
    try:
        _emit_progress(progress, f"workspace created: {workspace}", workspace=workspace)
        metadata = await probe_video_metadata(video_path)
        if mime_type:
            metadata.mime_type = mime_type
        _emit_progress(
            progress,
            f"video metadata loaded: duration={_seconds_to_label(metadata.duration_seconds)} "
            f"size={metadata.width or '?'}x{metadata.height or '?'} fps={metadata.fps or '?'}",
            workspace=workspace,
        )

        frames, frame_diagnostics = await extract_frame_samples(
            video_path,
            duration_seconds=metadata.duration_seconds,
            workspace=workspace,
        )
        _emit_progress(
            progress,
            f"frame extraction completed: {len(frames)} frames",
            workspace=workspace,
        )
        frames, frame_enrich_diagnostics = await enrich_frames(frames)
        _emit_progress(
            progress,
            f"frame enrichment completed: {len(frames)} frames",
            workspace=workspace,
        )

        transcripts, transcript_diagnostics, audio_incomplete = await transcribe_audio_segments(
            video_path,
            duration_seconds=metadata.duration_seconds,
            workspace=workspace,
            progress=progress,
        )

        diagnostics = [
            str(item).strip()
            for item in (
                frame_diagnostics + frame_enrich_diagnostics + transcript_diagnostics
            )
            if str(item).strip()
        ]
        fatal_audio_error = _extract_fatal_audio_error(diagnostics)
        if fatal_audio_error:
            return VideoTextResult(
                ok=False,
                source_video_path=str(video_path),
                mime_type=str(metadata.mime_type or mime_type or "video/mp4"),
                workspace_path=str(workspace),
                audio_track_path=_workspace_audio_track_path(workspace),
                segment_audio_dir=str((workspace / "audio" / "segments").resolve()),
                segment_text_dir=str((workspace / "audio" / "transcripts").resolve()),
                progress_log_path=str((workspace / "progress.log").resolve()),
                metadata=metadata,
                frame_count=len(frames),
                transcript_segment_count=len(transcripts),
                diagnostics=diagnostics,
                audio_incomplete=True,
                cached_file_id=str(file_id or ""),
                cached_platform=str(platform or ""),
            )
        markdown = render_markdown_artifact(
            video_path=video_path,
            metadata=metadata,
            frames=frames,
            transcripts=transcripts,
            diagnostics=diagnostics,
        )
        artifact_path.write_text(markdown, encoding="utf-8")
        _emit_progress(
            progress,
            f"artifact written: {artifact_path}",
            workspace=workspace,
        )

        result = VideoTextResult(
            ok=True,
            artifact_path=str(artifact_path),
            source_video_path=str(video_path),
            mime_type=str(metadata.mime_type or mime_type or "video/mp4"),
            workspace_path=str(workspace),
            audio_track_path=_workspace_audio_track_path(workspace),
            segment_audio_dir=str((workspace / "audio" / "segments").resolve()),
            segment_text_dir=str((workspace / "audio" / "transcripts").resolve()),
            progress_log_path=str((workspace / "progress.log").resolve()),
            metadata=metadata,
            frame_count=len(frames),
            transcript_segment_count=len(transcripts),
            diagnostics=diagnostics,
            audio_incomplete=audio_incomplete,
            cached_file_id=str(file_id or ""),
            cached_platform=str(platform or ""),
        )
        if file_id and platform:
            await save_cached_artifact(
                platform,
                file_id,
                artifact_path=result.artifact_path,
                source_video_path=result.source_video_path,
                mime_type=result.mime_type,
                workspace_path=result.workspace_path,
                audio_track_path=result.audio_track_path,
                segment_audio_dir=result.segment_audio_dir,
                segment_text_dir=result.segment_text_dir,
                progress_log_path=result.progress_log_path,
                duration_seconds=result.metadata.duration_seconds,
                frame_count=result.frame_count,
                transcript_segment_count=result.transcript_segment_count,
                diagnostics=result.diagnostics,
            )
        return result
    finally:
        pass


def build_forward_text(result: VideoTextResult, *, user_prompt: str) -> str:
    prompt = str(user_prompt or "").strip()
    lines = ["用户发送了一个视频。"]
    if prompt:
        lines.append(f"原始说明：{prompt}")
    lines.extend(
        [
            "系统已经先把视频转成了 Markdown 文本工件。",
            f"- 视频文件：{result.source_video_path}",
            f"- 文本工件：{result.artifact_path}",
            f"- 时长：{_seconds_to_label(result.metadata.duration_seconds)}",
            f"- 抽帧数：{result.frame_count}",
            f"- 音轨分段数：{result.transcript_segment_count}",
            "完整提取结果在该 Markdown 文件中。请按用户当前请求决定是否读取该文件继续处理。",
        ]
    )
    return "\n".join(lines).strip()


def build_reply_extra_context(result: VideoTextResult) -> str:
    lines = [
        "【引用视频文本工件】",
        f"- 视频文件：{result.source_video_path}",
        f"- 文本工件：{result.artifact_path}",
        f"- 时长：{_seconds_to_label(result.metadata.duration_seconds)}",
        f"- 抽帧数：{result.frame_count}",
        f"- 音轨分段数：{result.transcript_segment_count}",
        "完整提取结果在该 Markdown 文件中。如需继续分析，请读取该文件，不要假设已经直接看过原始视频。",
        "",
    ]
    if result.diagnostics:
        lines.append("【提取诊断】")
        for item in result.diagnostics:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines)


async def process_current_video_message(ctx: UnifiedContext) -> IncomingMediaInterceptResult:
    if not video_to_text_enabled():
        return IncomingMediaInterceptResult(handled=False)

    user_id = str(getattr(getattr(ctx.message, "user", None), "id", "") or "")
    if not await is_user_allowed(user_id):
        logger.info("Ignoring unauthorized video message from user_id=%s", user_id)
        return IncomingMediaInterceptResult(handled=True)
    if not await require_feature_access(ctx, "chat"):
        return IncomingMediaInterceptResult(handled=True)

    await ctx.reply("🎬 正在提取视频文本，请稍候...")
    await ctx.send_chat_action(action="typing")

    try:
        media = await extract_media_input(
            ctx,
            expected_types={MessageType.VIDEO},
            auto_download=False,
        )
    except MediaProcessingError as exc:
        if exc.error_code == "unsupported_media_on_platform":
            await ctx.reply("❌ 当前平台暂不支持该视频格式。")
        else:
            await ctx.reply("❌ 当前平台暂时无法下载视频内容，请稍后重试。")
        return IncomingMediaInterceptResult(handled=True)

    file_id = str(media.file_id or ctx.message.file_id or "").strip()
    platform = str(ctx.message.platform or "").strip().lower()

    cached = await get_cached_artifact(platform, file_id)
    if isinstance(cached, dict):
        artifact_path = str(cached.get("artifact_path") or "").strip()
        if artifact_path and Path(artifact_path).exists():
            cached_result = _result_from_cache(cached, platform=platform, file_id=file_id)
            return IncomingMediaInterceptResult(
                handled=True,
                forward_text=build_forward_text(
                    cached_result,
                    user_prompt=str(media.caption or ctx.message.text or "").strip(),
                ),
            )

    source_video_path = ""
    cached_source_path = await get_video_cache(file_id)
    if cached_source_path and Path(cached_source_path).exists():
        source_video_path = cached_source_path
    else:
        try:
            localized = await localize_video_message(
                ctx,
                file_id=file_id,
                mime_type=str(media.mime_type or "video/mp4"),
                file_name=str(media.file_name or ""),
            )
            source_video_path = str(localized)
        except Exception as exc:
            await ctx.reply(f"❌ 视频下载失败：{exc}")
            return IncomingMediaInterceptResult(handled=True)

    result = await ensure_video_artifact_for_path(
        source_video_path,
        file_id=file_id,
        platform=platform,
        mime_type=str(media.mime_type or "video/mp4"),
    )
    if not result.ok:
        detail = "；".join(result.diagnostics[:2]) or "未生成文本工件"
        await ctx.reply(f"❌ 视频文本化失败：{detail}")
        return IncomingMediaInterceptResult(handled=True)

    return IncomingMediaInterceptResult(
        handled=True,
        forward_text=build_forward_text(
            result,
            user_prompt=str(media.caption or ctx.message.text or "").strip(),
        ),
    )


async def provide_reply_video_context(
    ctx: UnifiedContext,
    reply_to: UnifiedMessage,
) -> ReplyContextHookResult:
    if not video_to_text_enabled():
        return ReplyContextHookResult(handled=False)

    file_id = str(getattr(reply_to, "file_id", "") or "").strip()
    platform = str(getattr(reply_to, "platform", "") or "").strip().lower()

    if not file_id:
        return ReplyContextHookResult(
            handled=True,
            extra_context=(
                "【系统提示】引用的是一个视频，但当前消息缺少可下载的文件标识，"
                "请明确告知用户无法读取该视频内容。\n\n"
            ),
            errors=["reply video missing file_id"],
        )

    await ctx.send_chat_action(action="typing")

    cached = await get_cached_artifact(platform, file_id)
    if isinstance(cached, dict):
        artifact_path = str(cached.get("artifact_path") or "").strip()
        if artifact_path and Path(artifact_path).exists():
            return ReplyContextHookResult(
                handled=True,
                extra_context=build_reply_extra_context(
                    _result_from_cache(cached, platform=platform, file_id=file_id)
                ),
            )

    source_video_path = ""
    cached_source_path = await get_video_cache(file_id)
    if cached_source_path and Path(cached_source_path).exists():
        source_video_path = cached_source_path
    else:
        try:
            localized = await localize_video_message(
                ctx,
                file_id=file_id,
                mime_type=str(getattr(reply_to, "mime_type", "") or "video/mp4"),
                file_name=str(getattr(reply_to, "file_name", "") or ""),
            )
            source_video_path = str(localized)
        except Exception as exc:
            return ReplyContextHookResult(
                handled=True,
                extra_context=(
                    "【系统提示】引用视频的本地化失败，请明确告知用户当前无法读取该视频内容。"
                    f"\n- 错误：{exc}\n\n"
                ),
                errors=[str(exc)],
            )

    result = await ensure_video_artifact_for_path(
        source_video_path,
        file_id=file_id,
        platform=platform,
        mime_type=str(getattr(reply_to, "mime_type", "") or "video/mp4"),
    )
    if not result.ok:
        detail = "；".join(result.diagnostics[:2]) or "未生成文本工件"
        return ReplyContextHookResult(
            handled=True,
            extra_context=(
                "【系统提示】引用视频的文本化失败，请明确告知用户当前无法读取该视频内容。"
                f"\n- 原因：{detail}\n\n"
            ),
            errors=list(result.diagnostics),
        )

    return ReplyContextHookResult(
        handled=True,
        extra_context=build_reply_extra_context(result),
    )


async def execute_video_to_text(
    *,
    path: str = "",
    ctx: UnifiedContext | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    if not video_to_text_enabled():
        return {
            "ok": False,
            "message": "video_to_text is disabled because VIDEO_TO_TEXT_WHISPER_ENDPOINT is not configured",
            "failure_mode": "non_recoverable",
            "text": "❌ video_to_text 未启用：未配置 VIDEO_TO_TEXT_WHISPER_ENDPOINT",
        }

    safe_path = str(path or "").strip()
    if safe_path:
        result = await ensure_video_artifact_for_path(safe_path, progress=progress)
    else:
        if ctx is None or getattr(getattr(ctx, "message", None), "type", None) != MessageType.VIDEO:
            return {
                "ok": False,
                "message": "path is required when current message is not a video",
                "failure_mode": "recoverable",
            }
        media = await extract_media_input(
            ctx,
            expected_types={MessageType.VIDEO},
            auto_download=False,
        )
        localized = await localize_video_message(
            ctx,
            file_id=str(media.file_id or ctx.message.file_id or ""),
            mime_type=str(media.mime_type or "video/mp4"),
            file_name=str(media.file_name or ""),
        )
        result = await ensure_video_artifact_for_path(
            localized,
            file_id=str(media.file_id or ""),
            platform=str(ctx.message.platform or ""),
            mime_type=str(media.mime_type or "video/mp4"),
            progress=progress,
        )

    payload = asdict(result)
    payload["metadata"] = asdict(result.metadata)
    payload["text"] = (
        f"✅ 已生成视频文本工件\n- 工件：{result.artifact_path}\n- 视频：{result.source_video_path}"
        if result.ok
        else f"❌ 视频文本化失败：{'；'.join(result.diagnostics[:2]) or 'unknown error'}"
    )
    return payload
