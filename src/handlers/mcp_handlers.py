import asyncio
import io
import logging
import os
import re
import shlex
import shutil
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from core.platform.models import UnifiedContext

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}


def _playwright_cli_command() -> list[str]:
    raw = str(os.getenv("PLAYWRIGHT_CLI_COMMAND") or "").strip()
    if raw:
        return [part for part in shlex.split(raw) if part]

    binary = shutil.which("playwright-cli")
    if binary:
        return [binary]

    allow_npx = str(os.getenv("PLAYWRIGHT_CLI_ALLOW_NPX", "false")).strip().lower()
    if allow_npx in _TRUTHY:
        return ["npx", "-y", "@playwright/cli@latest"]

    return []


async def _run_command(command: list[str], timeout_sec: float) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return 124, "", "timeout"

    return (
        int(proc.returncode or 0),
        stdout.decode("utf-8", errors="ignore"),
        stderr.decode("utf-8", errors="ignore"),
    )


def _extract_artifact_path(stdout_text: str, suffix: str) -> str:
    text = str(stdout_text or "")
    markdown = re.search(r"\[[^\]]+\]\(([^)]+\.%s)\)" % suffix, text, flags=re.I)
    if markdown:
        return str(markdown.group(1) or "").strip()

    plain = re.search(r"([^\s]+\.%s)" % suffix, text, flags=re.I)
    if plain:
        return str(plain.group(1) or "").strip()
    return ""


async def _capture_page_screenshot(url: str) -> tuple[bytes | None, str]:
    command_prefix = _playwright_cli_command()
    if not command_prefix:
        return None, "playwright-cli command not configured"

    session_id = f"xbot-shot-{uuid4().hex[:8]}"
    output_root = Path(os.getenv("PLAYWRIGHT_CLI_OUTPUT_DIR", "/tmp/xbot-playwright"))
    output_root.mkdir(parents=True, exist_ok=True)
    artifact_path = output_root / f"screenshot-{session_id}.png"

    browser = str(os.getenv("PLAYWRIGHT_CLI_BROWSER", "chrome") or "").strip()
    open_cmd = [*command_prefix, f"-s={session_id}", "open", url]
    if browser:
        open_cmd.append(f"--browser={browser}")
    screenshot_cmd = [
        *command_prefix,
        f"-s={session_id}",
        "screenshot",
        f"--filename={artifact_path}",
    ]
    close_cmd = [*command_prefix, f"-s={session_id}", "close"]

    open_timeout = float(os.getenv("PLAYWRIGHT_CLI_OPEN_TIMEOUT_SEC", "80"))
    shot_timeout = float(os.getenv("PLAYWRIGHT_CLI_SHOT_TIMEOUT_SEC", "80"))

    try:
        open_code, _open_out, open_err = await _run_command(
            open_cmd, timeout_sec=open_timeout
        )
        if open_code != 0:
            return None, (open_err or "playwright-cli open failed").strip()[:300]

        shot_code, shot_out, shot_err = await _run_command(
            screenshot_cmd,
            timeout_sec=shot_timeout,
        )
        if shot_code != 0:
            return None, (shot_err or "playwright-cli screenshot failed").strip()[:300]

        resolved = artifact_path
        if not resolved.exists():
            from_stdout = _extract_artifact_path(shot_out, "png")
            if from_stdout:
                maybe = Path(from_stdout)
                if not maybe.is_absolute():
                    maybe = Path.cwd() / maybe
                resolved = maybe

        if not resolved.exists() or not resolved.is_file():
            return None, "screenshot artifact not found"

        return resolved.read_bytes(), ""
    finally:
        try:
            await _run_command(close_cmd, timeout_sec=20)
        except Exception:
            pass


async def handle_browser_action(ctx: UnifiedContext, params: dict) -> bool:
    url = str(params.get("url") or "").strip()
    action = str(params.get("action") or "screenshot").strip().lower()

    if not url:
        await ctx.reply(
            "❌ 请提供要操作的网页 URL。\n\n示例：`截图 https://example.com`"
        )
        return True

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    if action != "screenshot":
        await ctx.reply(f"❌ 暂不支持的操作：`{action}`")
        return True

    return await _handle_screenshot(ctx, url)


async def _handle_screenshot(ctx: UnifiedContext, url: str) -> bool:
    thinking_msg = await ctx.reply(
        f"📸 正在截图 `{url}`...\n\n⏳ 首次使用可能需要较长时间"
    )

    if ctx.platform_ctx:
        try:
            await ctx.platform_ctx.bot.send_chat_action(
                chat_id=ctx.message.chat.id,
                action="upload_photo",
            )
        except Exception:
            pass

    screenshot_data, error = await _capture_page_screenshot(url)
    if screenshot_data:
        try:
            await thinking_msg.delete()
        except Exception:
            pass

        domain = urlparse(url).netloc.replace("www.", "") or "web"
        filename = f"screenshot_{domain}.png"
        payload = io.BytesIO(screenshot_data)
        payload.name = filename

        if ctx.platform_ctx:
            await ctx.platform_ctx.bot.send_document(
                chat_id=ctx.message.chat.id,
                document=payload,
                caption=f"📸 网页截图：{url}",
                parse_mode="Markdown",
            )
        else:
            await ctx.reply("✅ 截图已完成，但当前平台不支持发送附件。")
        return True

    hint = str(error or "截图失败")
    if "command not configured" in hint.lower():
        hint = (
            "未找到 playwright-cli 命令，请在环境中安装 `@playwright/cli` "
            "或配置 PLAYWRIGHT_CLI_COMMAND。"
        )
    elif "executable doesn't exist" in hint.lower() or "install" in hint.lower():
        hint = "浏览器尚未安装，请先执行 `playwright install chrome`。"

    try:
        await ctx.edit_message(
            thinking_msg.message_id,
            f"❌ 截图失败\n\n**URL**: `{url}`\n**原因**: {hint}",
        )
    except Exception:
        await ctx.reply(f"❌ 截图失败：{hint}")

    return True
