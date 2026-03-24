import logging
import os
import re
import aiofiles

from core.agent_input import (
    InlineInputResolution,
    MAX_INLINE_IMAGE_INPUTS,
    ReplyMessageResolution,
    ResolvedInlineInput,
    process_reply_message,
    resolve_inline_inputs_from_text,
    resolve_inline_inputs_from_urls,
)
from core.platform.models import UnifiedContext

logger = logging.getLogger(__name__)
CODE_BLOCK_FILE_MIN_LINES = 20


def _platform_code_block_file_extension(platform: str, language: str) -> str:
    safe_platform = str(platform or "").strip().lower()
    safe_language = str(language or "").strip().lower()

    # Weixin cannot open HTML attachments reliably. Keep code snippets as text.
    if safe_platform == "weixin":
        return "md"
    return "html" if safe_language == "html" else "md"


async def process_and_send_code_files(ctx: UnifiedContext, text: str) -> str:
    """
    1. Scan text for code blocks.
    2. If a code block is long enough, save as file and send to user.
    3. Replace the code block in the original text with a placeholder.
    4. Return the modified text for display.
    """
    if not text:
        return ""

    # Regex to find code blocks: ```language code ```
    code_block_regex = re.compile(r"```(\w+)?\n([\s\S]*?)```")
    matches = list(code_block_regex.finditer(text))

    if not matches:
        return text

    sent_count = 0
    temp_dir = "data/temp_code"
    os.makedirs(temp_dir, exist_ok=True)

    # We will rebuild the text with replacements
    final_text = text
    # Reverse iteration to avoiding index shifting when replacing
    for i, match in enumerate(reversed(matches)):
        # Calculate original index (since we are reversing)
        original_index = len(matches) - 1 - i

        start_pos, end_pos = match.span()
        language = match.group(1).lower().strip() if match.group(1) else "txt"
        code_content = match.group(2).strip()

        if not code_content:
            continue

        platform = getattr(ctx.message, "platform", "") or ""
        ext = _platform_code_block_file_extension(platform, language)

        lines = code_content.splitlines()
        should_process = len(lines) > CODE_BLOCK_FILE_MIN_LINES

        if not should_process:
            continue

        filename = f"code_snippet_{original_index + 1}.{ext}"
        filepath = os.path.join(temp_dir, filename)

        try:
            if ext == "html":
                file_content = code_content
                caption = "📝 HTML 代码片段"
            else:
                safe_lang = (
                    language if re.fullmatch(r"[a-zA-Z0-9_+\-]+", language) else ""
                )
                if language in {"md", "markdown"}:
                    file_content = code_content
                else:
                    fence = f"```{safe_lang}".rstrip()
                    file_content = f"{fence}\n{code_content}\n```"
                caption = f"📝 Markdown 文本片段（原始语言: {language}）"

            async with aiofiles.open(filepath, "w", encoding="utf-8") as f:
                await f.write(file_content)

            # Send document - 读取文件内容为 bytes 以确保跨平台兼容性
            async with aiofiles.open(filepath, "rb") as f:
                file_bytes = await f.read()

            # Platform-adaptive format conversion
            try:
                from services.md_converter import adapt_md_file_for_platform

                file_bytes, filename = adapt_md_file_for_platform(
                    file_bytes=file_bytes,
                    filename=filename,
                    platform=platform,
                )
            except Exception:
                pass

            await ctx.reply_document(
                document=file_bytes,
                filename=filename,
                caption=caption,
                reply_to_message_id=ctx.message.id,
            )
            sent_count += 1

            # Replace in text with placeholder
            placeholder = f"\n\n(⬇️ {language} 内容已保存为文件: {filename})\n\n"
            final_text = final_text[:start_pos] + placeholder + final_text[end_pos:]

        except Exception as e:
            logger.error(f"Failed to send code file {filename}: {e}")

    return final_text
