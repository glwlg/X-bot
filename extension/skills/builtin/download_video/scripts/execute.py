from __future__ import annotations

import argparse
import os
import asyncio
import logging
import sys
from pathlib import Path
from typing import Dict, Any

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
from user_context import add_message

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

    # Helper function handles finding platform_ctx internally or we pass logic
    # But stateless execute might not have interaction flow.
    # We'll reuse process_video_download which expects ctx.

    # We need to ensure process_video_download works.
    # It replies to ctx.
    await process_video_download(ctx, url, audio_only=(format_type == "audio"))

    return {"text": "🔇🔇🔇✅ 视频下载任务已提交", "ui": {}}


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

    await process_video_download(ctx, url, audio_only=(mode == "audio"))
    return None


async def process_video_download(
    ctx: UnifiedContext, url: str, audio_only: bool = False
) -> None:
    """
    Core video download logic, shared by direct command and AI router.
    """
    chat_id = ctx.message.chat.id
    user_id = ctx.message.user.id

    if not ctx.platform_ctx:
        logger.error("Platform context missing in process_video_download")
        return

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
        return

    file_path = result.file_path

    # 处理文件过大情况
    if result.is_too_large:
        # 暂存路径到 user_data以供后续操作
        ctx.user_data["large_file_path"] = file_path
        ui = {
            "actions": [
                [
                    {"text": "📝 生成内容摘要 (AI)", "callback_data": "large_file_summary"},
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
        return

    # 如果下载成功且大小合适，发送文件
    if file_path and os.path.exists(file_path):
        logger.info(f"Downloaded to {file_path}. Uploading to chat {chat_id}.")
        try:
            if audio_only:
                # 发送音频文件
                await ctx.reply_audio(
                    audio=open(file_path, "rb"), caption="🎵 仅音频 (视频提取)"
                )
            else:
                # 发送视频并获取返回的消息（包含 file_id）
                sent_message = await ctx.reply_video(
                    video=open(file_path, "rb"), supports_streaming=True
                )

                # 记录视频文件路径以供 AI 分析
                file_id = None
                if hasattr(sent_message, "video") and sent_message.video:
                    file_id = sent_message.video.file_id
                elif hasattr(sent_message, "attachments") and sent_message.attachments:
                    file_id = str(sent_message.attachments[0].id)
                elif hasattr(sent_message, "document") and sent_message.document:
                    file_id = sent_message.document.file_id

                if file_id:
                    from core.state_store import save_video_cache

                    await save_video_cache(file_id, file_path)
                    logger.info(f"Video cached: {file_id} -> {file_path}")

                # 记录统计
                from stats import increment_stat

                try:
                    await increment_stat(user_id, "downloads")
                except:
                    pass

            # 删除进度消息
            msg_id = getattr(
                processing_message,
                "message_id",
                getattr(processing_message, "id", None),
            )
            if msg_id:
                await ctx.delete_message(message_id=msg_id)

        except Exception as e:
            logger.error(f"Failed to send video to chat {chat_id}: {e}")
            msg_id = getattr(
                processing_message,
                "message_id",
                getattr(processing_message, "id", None),
            )
            if msg_id:
                await ctx.edit_message(
                    msg_id, "❌ 发送视频失败，可能是网络问题或格式不受支持。"
                )


async def handle_video_actions(ctx: UnifiedContext) -> None:
    """处理视频链接的智能选项（下载 vs 摘要）"""
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

        await process_video_download(ctx, url, audio_only=False)

    elif action == "action_summarize_video":
        try:
            await ctx.edit_message(ctx.message.id, "📄 正在获取网页内容并生成摘要...")
            await ctx.send_chat_action(action="typing")
        except Exception as e:
            logger.error(f"Error editing message in handle_video_actions: {e}")
            pass

        from services.web_summary_service import summarize_webpage

        summary = await summarize_webpage(url)

        try:
            await ctx.edit_message(ctx.message.id, summary)
        except Exception as e:
            logger.error(f"Error editing message in handle_video_actions: {e}")
            await ctx.reply(summary)

        # Save summary to history
        user_id = ctx.message.user.id
        try:
            await add_message(ctx.platform_ctx, user_id, "model", summary)
        except:
            pass

        # 统计
        from stats import increment_stat

        try:
            await increment_stat(user_id, "video_summaries")
        except:
            pass


async def handle_large_file_action(ctx: UnifiedContext) -> None:
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
                await ctx.reply_audio(
                    audio=open(final_path, "rb"),
                    caption="🎵 仅音频 (从大视频提取)",
                )
                try:
                    await ctx.delete_message(message_id=ctx.message.id)
                except:
                    pass

        elif data == "large_file_summary":
            await ctx.edit_message(
                ctx.message.id, "📝 正在提取并压缩音频，请稍候... (这可能需要几分钟)"
            )

            # Logic similar to original media_handlers.py
            # For brevity in this refactor I'm simplifying copy but assumption is standard ffmpeg available
            # ... (Full logic copied from media_handlers.py for summary)

            # Use ffmpeg to compress
            base, _ = os.path.splitext(file_path)
            compressed_audio_path = f"{base}_compressed.mp3"

            cmd = [
                "ffmpeg",
                "-i",
                file_path,
                "-vn",
                "-acodec",
                "libmp3lame",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-b:a",
                "32k",
                "-y",
                compressed_audio_path,
            ]
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await process.wait()

            if not os.path.exists(compressed_audio_path):
                await ctx.edit_message(ctx.message.id, "❌ 音频提取失败。")
                return

            import base64

            with open(compressed_audio_path, "rb") as f:
                audio_bytes = f.read()

            if len(audio_bytes) > 25 * 1024 * 1024:
                await ctx.edit_message(
                    ctx.message.id, "❌ 即使压缩后音频仍然过大，无法分析。"
                )
                try:
                    os.remove(compressed_audio_path)
                except:
                    pass
                return

            await ctx.edit_message(
                ctx.message.id, "📝 音频处理完成，正在通过 AI 生成摘要..."
            )
            from core.config import get_client_for_model
            from core.model_config import get_voice_model
            from services.openai_adapter import generate_text

            contents = [
                {
                    "parts": [
                        {
                            "text": "请详细总结这段视频音频的内容。请描述主要发生了什么，核心观点是什么，并列出关键时间点 (如果可能)。"
                        },
                        {
                            "inline_data": {
                                "mime_type": "audio/mp3",
                                "data": base64.b64encode(audio_bytes).decode("utf-8"),
                            }
                        },
                    ]
                }
            ]

            try:
                voice_model = get_voice_model()
                if not voice_model:
                    raise RuntimeError("No voice model configured in config/models.json")
                async_client = get_client_for_model(voice_model, is_async=True)
                if async_client is None:
                    raise RuntimeError("OpenAI async client is not initialized")
                summary_body = await generate_text(
                    async_client=async_client,
                    model=voice_model,
                    contents=contents,
                )
                if summary_body:
                    summary_text = f"📝 **视频内容摘要**\n\n{summary_body}"
                    await ctx.reply(summary_text)
                    await add_message(
                        ctx.platform_ctx, ctx.message.user.id, "model", summary_text
                    )
                    try:
                        await ctx.delete_message(message_id=ctx.message.id)
                    except:
                        pass
                else:
                    await ctx.edit_message(ctx.message.id, "❌ AI 无法生成摘要。")
            except Exception as e:
                await ctx.edit_message(ctx.message.id, f"❌ AI 分析失败: {e}")
            finally:
                try:
                    os.remove(compressed_audio_path)
                except:
                    pass

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
