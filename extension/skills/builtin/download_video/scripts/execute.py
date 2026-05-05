from __future__ import annotations

import argparse
import os
import asyncio
import logging
import sys
from pathlib import Path
from typing import Dict, Any

from core.file_artifacts import classify_file_kind

REPO_ROOT = Path(__file__).resolve().parents[5]
SRC_ROOT = REPO_ROOT / "src"
SCRIPT_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from core.platform.models import UnifiedContext
from core.skill_menu import make_callback, parse_callback
from core.config import is_user_allowed
from utils import extract_video_url

if __package__:
    from .services.download_service import download_video, get_download_dir
else:
    from services.download_service import download_video, get_download_dir

logger = logging.getLogger(__name__)
DOWNLOAD_MENU_NS = "dlm"


async def check_permission(ctx: UnifiedContext) -> bool:
    if not await is_user_allowed(ctx.message.user.id):
        return False
    return True


def _download_usage_text() -> str:
    return (
        "📹 **视频下载**\n\n"
        "直接发送以下命令：\n"
        "• `/download <视频链接>`\n"
        "• `/download video <视频链接>`\n"
        "• `/download audio <视频链接>`\n\n"
        "支持平台：X、YouTube、Instagram、TikTok、Bilibili。"
    )


def _download_menu_ui() -> dict:
    return {
        "actions": [
            [
                {"text": "📹 视频示例", "callback_data": make_callback(DOWNLOAD_MENU_NS, "videohelp")},
                {"text": "🎵 音频示例", "callback_data": make_callback(DOWNLOAD_MENU_NS, "audiohelp")},
            ]
        ]
    }


def _download_video_help() -> dict:
    return {
        "text": (
            "📹 **下载视频**\n\n"
            "直接发送：\n"
            "• `/download https://www.youtube.com/watch?v=xxx`\n"
            "• `/download video https://x.com/...`\n\n"
            "默认下载最佳可用视频。"
        ),
        "ui": {
            "actions": [
                [
                    {"text": "🎵 音频用法", "callback_data": make_callback(DOWNLOAD_MENU_NS, "audiohelp")},
                    {"text": "🏠 返回帮助", "callback_data": make_callback(DOWNLOAD_MENU_NS, "home")},
                ]
            ]
        },
    }


def _download_audio_help() -> dict:
    return {
        "text": (
            "🎵 **提取音频**\n\n"
            "直接发送：\n"
            "• `/download audio https://www.youtube.com/watch?v=xxx`\n\n"
            "这会优先返回 MP3 音频。"
        ),
        "ui": {
            "actions": [
                [
                    {"text": "📹 视频用法", "callback_data": make_callback(DOWNLOAD_MENU_NS, "videohelp")},
                    {"text": "🏠 返回帮助", "callback_data": make_callback(DOWNLOAD_MENU_NS, "home")},
                ]
            ]
        },
    }


def _parse_download_command(text: str) -> tuple[str, str]:
    raw = str(text or "").strip()
    if not raw:
        return "help", ""

    parts = raw.split(maxsplit=2)
    if not parts or not parts[0].startswith("/download"):
        return "help", ""
    if len(parts) == 1:
        return "help", ""

    sub = str(parts[1] or "").strip()
    lowered = sub.lower()
    if lowered in {"help", "h", "?"}:
        return "help", ""
    if lowered in {"audio", "mp3"}:
        return "audio", str(parts[2] if len(parts) >= 3 else "").strip()
    if lowered in {"video"}:
        return "video", str(parts[2] if len(parts) >= 3 else "").strip()
    return "video", " ".join(parts[1:]).strip()


# --- Skill Entry Point ---


async def execute(ctx: UnifiedContext, params: dict, runtime=None) -> Dict[str, Any]:
    """执行视频下载 (Stateless/AI called)"""
    url = params.get("url", "")
    format_type = params.get("format", "video")

    # Fallback: Try to extract URL from instruction if missing
    if not url and params.get("instruction"):
        import re

        match = re.search(r"(https?://[^\s]+)", params["instruction"])
        if match:
            url = match.group(0)

    if not url:
        return {"text": _download_usage_text(), "ui": _download_menu_ui()}

    return await process_video_download(ctx, url, audio_only=(format_type == "audio"))


async def download_command(ctx: UnifiedContext):
    """处理 /download 命令"""
    if not await check_permission(ctx):
        return None

    mode, raw_target = _parse_download_command(ctx.message.text or "")
    if mode == "help":
        return {"text": _download_usage_text(), "ui": _download_menu_ui()}

    url = extract_video_url(raw_target)
    if not url:
        return {
            "text": "❌ 未识别到有效视频链接。\n\n" + _download_usage_text(),
            "ui": _download_menu_ui(),
        }

    return await process_video_download(ctx, url, audio_only=(mode == "audio"))


def _build_download_file_payload(file_path: str, *, audio_only: bool = False) -> dict[str, str]:
    path = str(file_path or "").strip()
    filename = Path(path).name
    kind = "audio" if audio_only else classify_file_kind(filename)
    caption = "🎵 仅音频 (视频提取)" if audio_only else ""
    return {"path": path, "filename": filename, "kind": kind, "caption": caption}


async def _delete_message_safely(ctx: UnifiedContext, message: Any) -> None:
    msg_id = getattr(message, "message_id", getattr(message, "id", None))
    if not msg_id:
        return
    try:
        await ctx.delete_message(message_id=msg_id)
    except Exception:
        logger.debug("Failed to delete progress message", exc_info=True)


async def process_video_download(
    ctx: UnifiedContext, url: str, audio_only: bool = False
) -> Dict[str, Any]:
    """
    Core video download logic, shared by direct command and AI router.
    """
    chat_id = ctx.message.chat.id
    user_id = ctx.message.user.id

    if not ctx.platform_ctx:
        logger.error("Platform context missing in process_video_download")
        return {"text": "❌ 下载失败：缺少平台上下文。", "ui": {}}

    format_text = "音频" if audio_only else "视频"

    processing_message = await ctx.reply(f"正在下载{format_text}，请稍候... ⏳")

    # 下载视频/音频
    result = await download_video(
        url, chat_id, processing_message, audio_only=audio_only
    )

    if not result.success:
        if result.error_message:
            try:
                msg_id = getattr(
                    processing_message,
                    "message_id",
                    getattr(processing_message, "id", None),
                )
                if msg_id:
                    await ctx.edit_message(
                        msg_id, f"❌ 下载失败: {result.error_message}"
                    )
            except:
                pass
        return {"text": f"❌ 下载失败: {result.error_message or '未知错误'}", "ui": {}}

    file_path = result.file_path

    # 处理文件过大情况
    if result.is_too_large:
        # 暂存路径到 user_data以供后续操作
        ctx.user_data["large_file_path"] = file_path
        ui = {
            "actions": [
                [
                    {"text": "🎵 仅发送音频", "callback_data": "large_file_audio"},
                ],
                [
                    {"text": "🗑️ 删除文件", "callback_data": "large_file_delete"},
                ],
            ]
        }

        msg_id = getattr(
            processing_message, "message_id", getattr(processing_message, "id", None)
        )
        if msg_id:
            await ctx.edit_message(
                msg_id,
                f"⚠️ **视频文件过大 ({result.file_size_mb:.1f}MB)**\n\n"
                f"超过 Telegram 限制 (50MB)，无法直接发送。\n"
                f"您可以选择：",
                ui=ui,
            )
        return {
            "text": (
                f"⚠️ **视频文件过大 ({result.file_size_mb:.1f}MB)**\n\n"
                f"超过 Telegram 限制 (50MB)，无法直接发送。\n"
                f"您可以选择："
            ),
            "ui": ui,
        }

    if not file_path or not os.path.exists(file_path):
        return {"text": "❌ 下载失败：未找到下载后的文件。", "ui": {}}

    logger.info("Downloaded to %s. Returning file payload for unified delivery.", file_path)

    # 记录统计。文件发送交给统一交付链路，skill 本身不直接 reply_audio/reply_video。
    from stats import increment_stat

    try:
        await increment_stat(user_id, "downloads")
    except Exception:
        logger.debug("Failed to increment download stats", exc_info=True)

    await _delete_message_safely(ctx, processing_message)
    return {
        "text": f"✅ {format_text}下载完成。",
        "files": [_build_download_file_payload(file_path, audio_only=audio_only)],
        "ui": {},
    }


async def handle_video_actions(ctx: UnifiedContext) -> None:
    """处理视频链接的下载操作"""
    logger.info(f"🎬 [DownloadVideo] Received callback action: {ctx.callback_data}")
    await ctx.answer_callback()
    logger.info(ctx.message)

    if not ctx.platform_ctx:
        logger.error("Platform context not found")
        return

    url = ctx.user_data.get("pending_video_url")
    if not url:
        try:
            await ctx.edit_message(ctx.message.id, "❌ 链接已过期，请重新发送。")
        except:
            pass
        return

    action = ctx.callback_data
    if not action:
        return

    if action == "action_download_video":
        try:
            await ctx.edit_message(ctx.message.id, "📹 准备下载视频...")
        except Exception as e:
            logger.error(f"Error editing message in handle_video_actions: {e}")
            pass

        return await process_video_download(ctx, url, audio_only=False)


async def handle_large_file_action(ctx: UnifiedContext) -> Dict[str, Any] | None:
    """处理大文件操作的回调"""
    await ctx.answer_callback()

    # if not await check_permission(ctx):
    #     return

    data = ctx.callback_data
    file_path = ctx.user_data.get("large_file_path")

    if not file_path or not os.path.exists(file_path):
        try:
            await ctx.edit_message(
                ctx.message.id, "❌ 文件已过期或不存在，请重新下载。"
            )
        except:
            pass
        return

    chat_id = ctx.message.chat.id

    try:
        if data == "large_file_delete":
            try:
                os.remove(file_path)
            except:
                pass
            await ctx.edit_message(ctx.message.id, "🗑️ 文件已删除。")

        elif data == "large_file_audio":
            await ctx.edit_message(ctx.message.id, "🎵 正在提取音频并发送，请稍候...")
            base, ext = os.path.splitext(file_path)
            if ext.lower() == ".mp4":
                audio_path = f"{base}.mp3"
                if not os.path.exists(audio_path):
                    cmd = [
                        "ffmpeg",
                        "-i",
                        file_path,
                        "-vn",
                        "-acodec",
                        "libmp3lame",
                        "-q:a",
                        "4",
                        "-y",
                        audio_path,
                    ]
                    process = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await process.wait()

                final_path = audio_path
            else:
                final_path = file_path

            if os.path.getsize(final_path) > 50 * 1024 * 1024:
                await ctx.edit_message(
                    ctx.message.id, "❌ 提取的音频也超过 50MB，无法发送。"
                )
            else:
                await _delete_message_safely(ctx, ctx.message)
                return {
                    "text": "✅ 音频提取完成。",
                    "files": [
                        {
                            **_build_download_file_payload(final_path, audio_only=True),
                            "caption": "🎵 仅音频 (从大视频提取)",
                        }
                    ],
                    "ui": {},
                }

    except Exception as e:
        logger.error(f"Error handling large file action: {e}")
        await ctx.reply(f"❌ 操作失败: {str(e)}")


async def handle_download_menu_callback(ctx: UnifiedContext):
    data = ctx.callback_data
    if not data:
        return

    action, _parts = parse_callback(data, DOWNLOAD_MENU_NS)
    if not action:
        return

    await ctx.answer_callback()
    if action == "home":
        payload = {"text": _download_usage_text(), "ui": _download_menu_ui()}
    elif action == "videohelp":
        payload = _download_video_help()
    elif action == "audiohelp":
        payload = _download_audio_help()
    else:
        payload = {"text": "❌ 未知操作。", "ui": _download_menu_ui()}

    await ctx.edit_message(ctx.message.id, payload["text"], ui=payload.get("ui"))


def register_handlers(adapter_manager: Any):
    """Register stateless /download command and callbacks"""
    adapter_manager.on_command("download", download_command, description="下载视频或音频")
    adapter_manager.on_callback_query("^action_.*", handle_video_actions)
    adapter_manager.on_callback_query("^large_file_", handle_large_file_action)
    adapter_manager.on_callback_query("^dlm_", handle_download_menu_callback)


class _ConsoleProgressMessage:
    def __init__(self):
        self._last_text = ""

    async def edit_text(self, text: str):
        self._emit(text)

    async def edit(self, content: str | None = None, **_kwargs):
        self._emit(content or "")

    def _emit(self, text: str) -> None:
        payload = str(text or "").strip()
        if payload and payload != self._last_text:
            print(payload, file=sys.stderr)
            self._last_text = payload


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download video/audio into the project downloads directory.",
    )
    parser.add_argument("url", help="Media URL to download")
    parser.add_argument(
        "--format",
        choices=("video", "audio"),
        default="video",
        help="Output format. Default: video",
    )
    return parser


async def _run_cli() -> int:
    parser = _build_cli_parser()
    args = parser.parse_args()
    progress = _ConsoleProgressMessage()
    result = await download_video(
        str(args.url or "").strip(),
        user_id=0,
        progress_message=progress,
        audio_only=str(args.format or "video").strip().lower() == "audio",
    )
    if not result.success:
        print(result.error_message or "download failed", file=sys.stderr)
        return 1

    download_dir = get_download_dir()
    saved_path = str(result.file_path or "").strip()
    print(f"download_dir={download_dir}")
    if saved_path:
        print(f"saved_path={saved_path}")
    print(f"is_too_large={str(bool(result.is_too_large)).lower()}")
    if result.file_size_mb:
        print(f"file_size_mb={result.file_size_mb:.2f}")
    return 0


from core.extension_base import SkillExtension


class DownloadVideoSkillExtension(SkillExtension):
    name = "download_video_extension"
    skill_name = "download_video"

    def register(self, runtime) -> None:
        register_handlers(runtime.adapter_manager)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run_cli()))
