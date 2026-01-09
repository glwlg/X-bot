"""
视频下载模块
"""
import os
import re
import uuid
import time
import shutil
import asyncio
import logging
from telegram import Message
from telegram.error import BadRequest

from config import (
    DOWNLOAD_DIR,
    PERMANENT_STORAGE_DIR,
    UPDATE_INTERVAL_SECONDS,
    MAX_FILE_SIZE_MB,
)
from utils import create_progress_bar

logger = logging.getLogger(__name__)


async def download_video(url: str, user_id: int, progress_message: Message, audio_only: bool = False) -> str | None:
    """
    从给定 URL 下载视频或音频，提供进度更新，检查文件大小
    
    Args:
        url: 视频 URL
        user_id: 用户 ID（用于日志）
        progress_message: 用于显示进度的消息对象
        audio_only: 如果为 True，只下载音频（MP3 格式）
        
    Returns:
        如果文件可发送则返回文件路径，否则返回 None
    """
    mode_str = "audio" if audio_only else "video"
    logger.info(f"[{user_id}] Attempting to download {mode_str} from URL: {url}")

    # 使用 URL 哈希作为文件名，避免重复下载
    import hashlib
    url_hash = hashlib.md5(url.encode()).hexdigest()
    
    if audio_only:
        filename_base = f"audio_{url_hash}"
        # mp3 是我们指定的格式
        expected_filename = f"{filename_base}.mp3"
        output_template = os.path.join(DOWNLOAD_DIR, f"{filename_base}.%(ext)s")
    else:
        filename_base = f"video_{url_hash}"
        # mp4 是我们 merge 的格式
        expected_filename = f"{filename_base}.mp4"
        output_template = os.path.join(DOWNLOAD_DIR, f"{filename_base}.%(ext)s")
    
    # 检查文件是否已存在
    expected_path = os.path.join(DOWNLOAD_DIR, expected_filename)
    if os.path.exists(expected_path):
        logger.info(f"[{user_id}] File already exists: {expected_path}")
        return expected_path

    if audio_only:
        command = [
            "yt-dlp",
            "--progress",
            "--newline",
            "--js-runtimes",
            "node",
            "-x",  # 提取音频
            "--audio-format",
            "mp3",
            "--audio-quality",
            "0",  # 最佳质量
            "-o",
            output_template,
            url,
        ]
    else:
        command = [
            "yt-dlp",
            "--progress",
            "--newline",
            "--js-runtimes",
            "node",
            "-f",
            "bestvideo+bestaudio/best",
            "--merge-output-format",
            "mp4",
            "-o",
            output_template,
            url,
        ]

    logger.info(f"[{user_id}] Running command: {' '.join(command)}")
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # 实时更新进度
    await _update_download_progress(proc, progress_message)

    await proc.wait()

    stderr_output = await proc.stderr.read()
    if stderr_output:
        logger.warning(
            f"[{user_id}] yt-dlp stderr:\n{stderr_output.decode('utf-8', errors='ignore')}"
        )

    if proc.returncode != 0:
        logger.error(
            f"[{user_id}] yt-dlp failed for URL {url} with return code {proc.returncode}."
        )
        error_line = stderr_output.decode("utf-8", errors="ignore").strip().splitlines()[
            -1
        ]
        try:
            await progress_message.edit_text(f"❌ 下载失败\n{error_line}")
        except Exception:
            pass
        return None

    # 检查文件大小并处理
    return await _handle_downloaded_file(expected_path, user_id, progress_message)


async def _update_download_progress(proc, progress_message: Message) -> None:
    """更新下载进度"""
    last_update_time = 0
    current_progress_text = ""
    percentage_regex = re.compile(r"\[download\]\s+(\d+\.?\d+)%")

    async for line in proc.stdout:
        line_str = line.decode("utf-8", errors="ignore").strip()
        match = percentage_regex.search(line_str)

        if match:
            now = time.time()
            if now - last_update_time > UPDATE_INTERVAL_SECONDS:
                try:
                    percentage = float(match.group(1))
                    new_text = create_progress_bar(percentage)
                    if new_text != current_progress_text:
                        await progress_message.edit_text(new_text)
                        current_progress_text = new_text
                        last_update_time = now
                except BadRequest as e:
                    if "Message is not modified" not in str(e):
                        logger.warning(f"Failed to edit progress message: {e}")
                except Exception as e:
                    logger.error(
                        f"An unexpected error occurred while editing message: {e}"
                    )


async def _handle_downloaded_file(
    file_path: str, user_id: int, progress_message: Message
) -> str | None:
    """处理下载完成的文件，检查大小并决定是否可发送"""
    try:
        if not os.path.exists(file_path):
            logger.error(
                f"[{user_id}] yt-dlp succeeded but file not found: {file_path}"
            )
            await progress_message.edit_text("❌ 下载后未找到文件。")
            return None

        file_size_bytes = os.path.getsize(file_path)
        file_size_mb = file_size_bytes / (1024 * 1024)

        logger.info(
            f"[{user_id}] Downloaded file: {file_path}, Size: {file_size_mb:.2f} MB"
        )

        # 如果文件太大，移动到永久存储（虽然现在已经在 storage 中，但为了区分无法发送的文件，还是移动一下？）
        # 或者直接报错不移动，或者保留原地？
        # 原逻辑是移走。我们保持原逻辑，但也可能是为了避免占位？
        if file_size_mb > MAX_FILE_SIZE_MB:
            os.makedirs(PERMANENT_STORAGE_DIR, exist_ok=True)
            new_path = os.path.join(
                PERMANENT_STORAGE_DIR, os.path.basename(file_path)
            )
            logger.info(
                f"File is too large ({file_size_mb:.2f} MB). Moving to {new_path}"
            )
            shutil.move(file_path, new_path)
            await progress_message.edit_text(
                f"❌ 视频文件过大 ({file_size_mb:.2f} MB)，无法发送。"
            )
            return None

        # 文件大小合适，可以发送
        await progress_message.edit_text("✅ 下载完成，正在上传...")
        return file_path

    except Exception as e:
        logger.error(f"[{user_id}] Error during file size check or move: {e}")
        await progress_message.edit_text("❌ 处理文件时出错。")
        return None
