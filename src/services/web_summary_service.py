"""
网页摘要模块 - 提取网页内容并使用 AI 生成摘要
"""

import re
import logging
import asyncio
import os
import shlex
import shutil
import httpx
from pathlib import Path
from uuid import uuid4

from core.config import get_client_for_model
from core.model_config import get_current_model
from services.openai_adapter import generate_text_sync

logger = logging.getLogger(__name__)

# URL 正则表达式
URL_PATTERN = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+')


def extract_urls(text: str) -> list[str]:
    """从文本中提取 URL"""
    return URL_PATTERN.findall(text)


def _as_bool(value: str, *, default: bool = False) -> bool:
    rendered = str(value or "").strip().lower()
    if rendered in {"1", "true", "yes", "on"}:
        return True
    if rendered in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _playwright_cli_command() -> list[str]:
    raw = str(os.getenv("PLAYWRIGHT_CLI_COMMAND") or "").strip()
    if raw:
        return [part for part in shlex.split(raw) if part]
    binary = shutil.which("playwright-cli")
    if binary:
        return [binary]
    allow_npx = str(os.getenv("PLAYWRIGHT_CLI_ALLOW_NPX", "false")).lower()
    if allow_npx in {"1", "true", "yes", "on"}:
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


def _extract_snapshot_path(snapshot_stdout: str) -> str:
    text = str(snapshot_stdout or "")
    match = re.search(r"\[Snapshot\]\(([^)]+)\)", text)
    if not match:
        return ""
    return str(match.group(1) or "").strip()


async def fetch_with_playwright_cli_snapshot(url: str) -> str | None:
    command_prefix = _playwright_cli_command()
    if not command_prefix:
        return None

    session_id = f"xbot-{uuid4().hex[:8]}"
    output_root = Path(os.getenv("PLAYWRIGHT_CLI_OUTPUT_DIR", "/tmp/xbot-playwright"))
    output_root.mkdir(parents=True, exist_ok=True)
    snapshot_file = output_root / f"snapshot-{session_id}.yml"

    browser = str(os.getenv("PLAYWRIGHT_CLI_BROWSER", "chrome") or "").strip()
    open_command = [*command_prefix, f"-s={session_id}", "open", url]
    if browser:
        open_command.append(f"--browser={browser}")

    close_command = [*command_prefix, f"-s={session_id}", "close"]
    try:
        open_code, _open_out, open_err = await _run_command(
            open_command, timeout_sec=60
        )
        if open_code != 0:
            logger.warning(
                "playwright-cli open failed for %s: %s",
                url,
                open_err.strip()[:300],
            )
            return None

        snapshot_command = [
            *command_prefix,
            f"-s={session_id}",
            "snapshot",
            f"--filename={snapshot_file}",
        ]
        snapshot_code, snapshot_out, snapshot_err = await _run_command(
            snapshot_command,
            timeout_sec=60,
        )
        if snapshot_code != 0:
            logger.warning(
                "playwright-cli snapshot failed for %s: %s",
                url,
                snapshot_err.strip()[:300],
            )
            return None

        resolved_path = snapshot_file
        if not resolved_path.exists():
            extracted = _extract_snapshot_path(snapshot_out)
            if extracted:
                maybe_path = Path(extracted)
                if not maybe_path.is_absolute():
                    maybe_path = Path.cwd() / maybe_path
                resolved_path = maybe_path

        if not resolved_path.exists() or not resolved_path.is_file():
            logger.warning("playwright-cli snapshot file not found for %s", url)
            return None

        content = resolved_path.read_text(encoding="utf-8", errors="ignore").strip()
        if not content:
            return None
        return f"【通过 Playwright CLI 获取的页面 Markdown 快照】\n\n{content}"
    except Exception as exc:
        logger.warning("playwright-cli fetch failed for %s: %s", url, exc)
        return None
    finally:
        try:
            await _run_command(close_command, timeout_sec=20)
        except Exception:
            pass


async def fetch_with_jina_reader(url: str) -> str | None:
    """使用 Jina Reader 提取网页 Markdown 内容"""
    try:
        jina_url = f"https://r.jina.ai/{url}"
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(
                jina_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "text/markdown",
                },
            )
            response.raise_for_status()
            text = response.text.strip()
            if not text:
                return None
            return f"【通过 Jina Reader 获取的页面 Markdown 快照】\n\n{text}"
    except Exception as e:
        logger.warning(f"Jina Reader fetch failed for {url}: {e}")
        return None


async def fetch_with_http_raw(url: str) -> str | None:
    """直接抓取原始 HTTP 页面内容。"""
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                },
            )
            response.raise_for_status()
            text = str(response.text or "").strip()
            if not text:
                return None
            return f"【HTTP 原始页面内容】\n\n{text}"
    except Exception as e:
        logger.warning(f"HTTP fetch failed for {url}: {e}")
        return None


async def fetch_webpage_content(url: str) -> str | None:
    """
    获取网页内容

    Args:
        url: 网页 URL

    Returns:
        网页文本内容，如果失败返回 None
    """

    prefer_cli = _as_bool(os.getenv("WEB_BROWSER_PREFER_PLAYWRIGHT_CLI", "true"))
    if prefer_cli:
        cli_content = await fetch_with_playwright_cli_snapshot(url)
        if cli_content:
            return cli_content

        jina_content = await fetch_with_jina_reader(url)
        if jina_content:
            return jina_content

        http_content = await fetch_with_http_raw(url)
        if http_content:
            return http_content
    else:
        http_content = await fetch_with_http_raw(url)
        if http_content:
            return http_content

        jina_content = await fetch_with_jina_reader(url)
        if jina_content:
            return jina_content

        cli_content = await fetch_with_playwright_cli_snapshot(url)
        if cli_content:
            return cli_content

    logger.warning("All scraping methods failed or unavailable for: %s", url)
    return None


async def summarize_webpage(url: str) -> str:
    """
    获取网页并生成摘要

    Args:
        url: 网页 URL

    Returns:
        摘要文本
    """
    # 获取网页内容
    content = await fetch_webpage_content(url)
    if not content:
        return f"❌ 无法获取网页内容：{url}"

    try:
        prompt = f"请为以下网页内容生成简洁的中文摘要：\n\n{content}"
        system_instruction = (
            "你是一个专业的内容摘要助手。"
            "请生成简洁、准确的中文摘要，包含以下要点：\n"
            "1. 主题是什么\n"
            "2. 主要观点或内容\n"
            "3. 关键信息\n"
            "摘要应该简洁明了，一般不超过 200 字。"
        )

        model_to_use = get_current_model()
        client_to_use = get_client_for_model(model_to_use, is_async=False)
        if client_to_use is None:
            raise RuntimeError("OpenAI sync client is not initialized")
        summary = generate_text_sync(
            sync_client=client_to_use,
            model=model_to_use,
            contents=prompt,
            config={
                "system_instruction": system_instruction,
            },
        )

        if summary:
            return f"📄 **网页摘要**\n\n🔗 {url}\n\n{summary}"
        else:
            return f"❌ 无法生成摘要：{url}"

    except Exception as e:
        logger.error(f"Failed to summarize webpage: {e}")
        return f"❌ 摘要生成失败：{url}"
