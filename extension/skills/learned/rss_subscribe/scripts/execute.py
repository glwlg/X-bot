"""RSS Subscription Skill Script."""

from __future__ import annotations

import argparse
import asyncio
import datetime
import hashlib
import html
import logging
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
SRC_ROOT = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import feedparser
import httpx
from core.channel_access import channel_feature_denied_text, is_channel_feature_enabled
from core.platform.models import UnifiedContext
from core.skill_menu import make_callback, parse_callback
from core.skill_cli import (
    add_common_arguments,
    merge_params,
    prepare_default_env,
    run_execute_cli,
)

prepare_default_env(REPO_ROOT)

from core.state_store import (
    create_subscription,
    delete_subscription,
    get_feature_delivery_target,
    list_feed_subscriptions,
    get_subscription,
    list_subscriptions,
    set_feature_delivery_target,
    update_feed_subscription_state,
)
from core.state_paths import SINGLE_USER_SCOPE
from stats import increment_stat

logger = logging.getLogger(__name__)
RSS_MENU_NS = "rssm"
FEED_CHECK_INTERVAL_SEC = 30 * 60
_rss_check_lock = asyncio.Lock()


def _rss_enabled(ctx: UnifiedContext) -> bool:
    return is_channel_feature_enabled(
        platform=str(ctx.message.platform or "").strip().lower(),
        platform_user_id=str(ctx.message.user.id or "").strip(),
        feature="rss",
    )


def _format_delivery_target(target: dict[str, str] | None) -> str:
    platform = str((target or {}).get("platform") or "").strip()
    chat_id = str((target or {}).get("chat_id") or "").strip()
    if not platform or not chat_id:
        return "未设置"
    return f"{platform}:{chat_id}"


def _current_delivery_target(ctx: UnifiedContext) -> dict[str, str]:
    return {
        "platform": str(ctx.message.platform or "telegram").strip() or "telegram",
        "chat_id": str(getattr(ctx.message.chat, "id", "") or "").strip(),
    }


async def _ensure_default_rss_delivery_target(
    ctx: UnifiedContext,
    user_id: int | str,
) -> dict[str, str]:
    current = await get_feature_delivery_target(user_id, "rss")
    if current:
        return current
    target = _current_delivery_target(ctx)
    if target["platform"] and target["chat_id"]:
        return await set_feature_delivery_target(
            user_id,
            "rss",
            target["platform"],
            target["chat_id"],
        )
    return {}


def _parse_rss_subcommand(text: str) -> tuple[str, str]:
    raw = str(text or "").strip()
    if not raw:
        return "menu", ""

    parts = raw.split(maxsplit=2)
    if not parts:
        return "list", ""
    if not parts[0].startswith("/rss"):
        return "help", ""
    if len(parts) == 1:
        return "menu", ""

    sub = str(parts[1] or "").strip().lower()
    args = str(parts[2] if len(parts) >= 3 else "").strip()

    if sub in {"menu", "home", "start"}:
        return "menu", ""
    if sub in {"list", "ls", "show"}:
        return "list", ""
    if sub in {"add", "subscribe", "sub"}:
        return "add", args
    if sub in {"monitor", "news", "keyword"}:
        return "monitor", args
    if sub in {"remove", "unsubscribe", "rm", "del", "delete"}:
        return "remove", args
    if sub in {"refresh", "check", "run", "latest"}:
        return "refresh", ""
    if sub in {"help", "h", "?"}:
        return "help", ""
    return "help", ""


def _rss_usage_text() -> str:
    return (
        "用法:\n"
        "`/rss`\n"
        "`/rss list`\n"
        "`/rss add <RSS URL>`\n"
        "`/rss remove <订阅ID>`\n"
        "`/rss refresh`\n"
        "`/rss help`"
    )


def _rss_menu_ui() -> dict:
    return {
        "actions": [
            [
                {"text": "📋 订阅列表", "callback_data": make_callback(RSS_MENU_NS, "list")},
                {"text": "🔄 立即检查", "callback_data": make_callback(RSS_MENU_NS, "refresh")},
            ],
            [
                {"text": "📍 设为当前渠道", "callback_data": make_callback(RSS_MENU_NS, "bind")},
                {"text": "❌ 取消订阅", "callback_data": make_callback(RSS_MENU_NS, "remove")},
            ],
            [
                {"text": "➕ 如何订阅", "callback_data": make_callback(RSS_MENU_NS, "addhelp")},
            ],
        ]
    }


async def show_rss_menu(ctx: UnifiedContext) -> dict:
    subs = await list_subscriptions(ctx.message.user.id)
    delivery_target = await get_feature_delivery_target(ctx.message.user.id, "rss")
    preview = "、".join(
        str(sub.get("title") or "").strip()
        for sub in subs[:3]
        if str(sub.get("title") or "").strip()
    )
    if len(subs) > 3:
        preview += " 等"
    if not preview:
        preview = "暂无订阅"

    return {
        "text": (
            "📰 **RSS 订阅管理**\n\n"
            f"当前订阅数：{len(subs)}\n"
            f"当前订阅：{preview}\n\n"
            f"推送渠道：`{_format_delivery_target(delivery_target)}`\n\n"
            "支持直接输入：`/rss add <RSS URL>`、`/rss remove <订阅ID>`。"
        ),
        "ui": _rss_menu_ui(),
    }


def _rss_add_help_response() -> dict:
    return {
        "text": (
            "➕ **添加 RSS 订阅**\n\n"
            "直接发送：\n"
            "• `/rss add https://example.com/feed.xml`\n"
            "• `/rss add https://blog.example.com/rss`\n\n"
            "只支持真实 RSS/Atom 链接，不支持关键词监控。"
        ),
        "ui": {
            "actions": [
                [
                    {"text": "🏠 返回首页", "callback_data": make_callback(RSS_MENU_NS, "home")},
                    {"text": "📋 订阅列表", "callback_data": make_callback(RSS_MENU_NS, "list")},
                ]
            ]
        },
    }


def _normalize_action(value: str) -> str:
    action = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    action_alias = {
        "list_subscriptions": "list",
        "list_subscription": "list",
        "list_all_feeds": "list",
        "list_feeds": "list",
        "show_subscriptions": "list",
        "show_list": "list",
        "check_updates": "refresh",
        "list_updates": "refresh",
        "fetch_latest": "refresh",
        "check_latest": "refresh",
        "remove_subscription": "remove",
        "delete_subscription": "remove",
    }
    action = action_alias.get(action, action)
    if action in {"check", "run", "latest"}:
        return "refresh"
    return action


def _extract_target(params: dict) -> str:
    raw_target = str(
        params.get("url")
        or params.get("keyword")
        or params.get("target")
        or params.get("query")
        or ""
    ).strip()
    command_token = raw_target
    if command_token.startswith("/"):
        command_token = command_token[1:]
    token = command_token.strip().lower()
    if token in {"list", "list_subs", "subscriptions", "subs"}:
        return ""
    return raw_target


async def execute(ctx: UnifiedContext, params: dict, runtime=None) -> dict:
    if not _rss_enabled(ctx):
        return {"text": channel_feature_denied_text("rss"), "ui": {}}
    action = _normalize_action(str(params.get("action") or ""))
    raw_target = str(
        params.get("url")
        or params.get("keyword")
        or params.get("target")
        or params.get("query")
        or ""
    ).strip()
    target = _extract_target(params)
    message_text = str(getattr(getattr(ctx, "message", None), "text", "") or "").lower()

    if not action:
        raw_token = raw_target.lower().lstrip("/")
        if raw_token in {"list", "list_subs", "subscriptions", "subs"}:
            action = "list"
            target = ""
        elif raw_token in {"refresh", "check", "run", "latest"}:
            action = "refresh"
            target = ""
        elif raw_token in {"remove", "unsubscribe", "delete"}:
            action = "remove"
            target = ""
        else:
            token = str(target or "").strip().lower().lstrip("/")
            if token in {"list", "list_subs", "subscriptions", "subs"}:
                action = "list"
                target = ""
            elif token in {"refresh", "check", "run", "latest"}:
                action = "refresh"
                target = ""
            elif token in {"remove", "unsubscribe", "delete"}:
                action = "remove"
                target = ""
            else:
                action = "refresh" if not target else "add"

    if action == "list":
        update_tokens = ("更新", "最新", "check", "refresh", "update", "latest", "有没有")
        explicit_list_only_tokens = ("只看列表", "仅列表", "list only", "只列出")
        has_update_goal = any(token in message_text for token in update_tokens)
        list_only_goal = any(token in message_text for token in explicit_list_only_tokens)
        if has_update_goal and not list_only_goal:
            action = "refresh"

    if action == "refresh":
        return {"text": await refresh_user_subscriptions(ctx), "ui": {}}
    if action == "list":
        return await list_subs_command(ctx)
    if action == "remove":
        if target:
            return await remove_subscription_by_target(ctx, target)
        return await show_unsubscribe_menu(ctx)
    if action == "monitor":
        return {"text": "❌ 关键词监控已下线，仅支持真实 RSS/Atom 订阅。", "ui": {}}
    if action == "add":
        if not target:
            return {"text": "用法: `/rss add <RSS URL>`", "ui": {}}
        return await process_subscribe(ctx, target)
    return {"text": _rss_usage_text(), "ui": {}}


def register_handlers(adapter_manager):
    """注册 RSS 二级命令和 Callback"""
    from core.config import is_user_allowed

    async def cmd_rss(ctx):
        if not await is_user_allowed(ctx.message.user.id):
            return
        if not _rss_enabled(ctx):
            return {"text": channel_feature_denied_text("rss"), "ui": {}}

        sub, args = _parse_rss_subcommand(ctx.message.text or "")
        if sub == "menu":
            return await show_rss_menu(ctx)
        if sub == "list":
            return await list_subs_command(ctx, include_menu_nav=True)
        if sub == "add":
            if not args.strip():
                return {"text": "用法: `/rss add <RSS URL>`", "ui": {}}
            return await process_subscribe(ctx, args.strip())
        if sub == "monitor":
            return {"text": "❌ 关键词监控已下线，仅支持真实 RSS/Atom 订阅。", "ui": {}}
        if sub == "remove":
            if args.strip():
                return await remove_subscription_by_target(ctx, args.strip())
            return await show_unsubscribe_menu(ctx, include_menu_nav=True)
        if sub == "refresh":
            return {"text": await refresh_user_subscriptions(ctx), "ui": _rss_menu_ui()}
        return {"text": _rss_usage_text(), "ui": {}}

    adapter_manager.on_command("rss", cmd_rss, description="RSS 订阅管理")
    adapter_manager.on_callback_query("^unsub_", handle_unsubscribe_callback)
    adapter_manager.on_callback_query("^rssm_", handle_unsubscribe_callback)


async def generate_entry_summary(title: str, content: str, link: str) -> str:
    """使用 AI 生成 RSS 条目摘要。"""
    from core.config import get_client_for_model
    from core.llm_usage_store import llm_usage_session
    from core.model_config import get_current_model
    from services.openai_adapter import generate_text

    if len(content) > 2000:
        content = content[:2000] + "..."

    prompt = (
        "请为以下内容生成一段简洁的中文摘要。\n"
        "规则：\n"
        "1. 如果内容丰富，生成 100-150 字的摘要，突出核心信息。\n"
        "2. 如果内容非常简短（如 GitHub Commit 信息、只有一句话的动态），请直接复述或翻译该内容，不要抱怨信息量不足，也不要试图强行扩展。\n"
        "3. 直接输出摘要内容，不要加任何前缀。\n\n"
        f"**标题**：{title}\n\n"
        f"**内容**：{content}"
    )

    try:
        model_to_use = get_current_model()
        client_to_use = get_client_for_model(model_to_use, is_async=True)
        if client_to_use is None:
            raise RuntimeError("OpenAI async client is not initialized")
        with llm_usage_session("system:rss_summary"):
            summary = await generate_text(
                async_client=client_to_use,
                model=model_to_use,
                contents=prompt,
            )
        return str(summary or "").strip()
    except Exception as exc:
        logger.error("AI summary generation failed: %s", exc)
        return content[:200] + "..." if len(content) > 200 else content


def _extract_urls_from_html(raw_html: str) -> list[str]:
    text = str(raw_html or "")
    if not text:
        return []
    matches = re.findall(r'href=["\'](https?://[^"\']+)["\']', text, flags=re.I)
    urls: list[str] = []
    for item in matches:
        value = html.unescape(str(item or "").strip())
        if value:
            urls.append(value)
    return urls


def _resolve_entry_link(entry: object, fallback_url: str) -> str:
    fallback = str(fallback_url or "").strip()

    def _entry_get(key: str, default: object = "") -> object:
        getter = getattr(entry, "get", None)
        if callable(getter):
            return getter(key, default)
        return getattr(entry, key, default)

    primary = str(_entry_get("link", "") or "").strip()
    candidates: list[str] = []

    links = _entry_get("links", [])
    if isinstance(links, list):
        for item in links:
            href = ""
            if isinstance(item, dict):
                href = str(item.get("href") or "").strip()
            else:
                href = str(getattr(item, "href", "") or "").strip()
            if href:
                candidates.append(html.unescape(href))

    for field in ("summary", "description"):
        value = str(_entry_get(field, "") or "")
        candidates.extend(_extract_urls_from_html(value))

    content_list = _entry_get("content", [])
    if isinstance(content_list, list):
        for item in content_list:
            blob = ""
            if isinstance(item, dict):
                blob = str(item.get("value") or "")
            else:
                blob = str(getattr(item, "value", "") or "")
            candidates.extend(_extract_urls_from_html(blob))

    seen: set[str] = set()
    normalized: list[str] = []
    for item in candidates:
        url = str(item or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        normalized.append(url)

    if primary and primary not in seen:
        normalized.insert(0, primary)

    for url in normalized:
        if url.startswith("http"):
            return url
    return primary or fallback


def _get_entry_hash(entry: object, _feed_url: str) -> str:
    title = str(getattr(entry, "get", lambda *x: "")("title", "") or "")
    if title:
        return hashlib.md5(title.encode("utf-8")).hexdigest()

    fallback_id = str(getattr(entry, "id", getattr(entry, "link", None)) or "")
    if fallback_id:
        return hashlib.md5(fallback_id.encode("utf-8")).hexdigest()
    return ""


def _format_feed_updates(updates: list[dict[str, str]]) -> str:
    if not updates:
        return ""
    lines = [f"📢 **RSS 更新 ({len(updates)} 条)**", ""]
    for update in updates:
        lines.append(f"🔹 **{update['feed_title']}**")
        lines.append(f"[{update['title']}]({update['link']})")
        lines.append(f"📝 {update['summary']}")
        lines.append("")
    return "\n".join(lines).strip()


def _split_message_batches(
    header: str, items: list[str], *, limit: int = 4000
) -> list[str]:
    batches: list[str] = []
    current = header
    for item in items:
        if len(current) + len(item) + 1 > limit and current != header:
            batches.append(current.rstrip())
            current = header
        current += item
    if current.strip():
        batches.append(current.rstrip())
    return batches


async def _fetch_feed_updates(
    *,
    user_id: int | str | None = None,
    subscriptions: list[dict[str, object]] | None = None,
) -> tuple[
    str, list[dict[str, object]], dict[tuple[str, str], list[dict[str, object]]]
]:
    if subscriptions is None:
        subscriptions = (
            await list_subscriptions(user_id)
            if user_id is not None
            else await list_feed_subscriptions()
        )
    subscriptions = list(subscriptions or [])
    if not subscriptions:
        return "", [], {}

    rss_delivery_target = await get_feature_delivery_target(SINGLE_USER_SCOPE, "rss")
    feed_map: dict[str, list[dict[str, object]]] = {}
    for sub in subscriptions:
        url = str(sub.get("feed_url") or "").strip()
        if url:
            feed_map.setdefault(url, []).append(sub)

    user_updates: dict[tuple[str, str], list[dict[str, object]]] = {}
    pending_updates: list[dict[str, object]] = []
    loop = asyncio.get_running_loop()

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        for url, subs in feed_map.items():
            request_headers: dict[str, str] = {}
            first_sub = subs[0]
            if str(first_sub.get("last_etag") or "").strip():
                request_headers["If-None-Match"] = str(first_sub.get("last_etag") or "")
            if str(first_sub.get("last_modified") or "").strip():
                request_headers["If-Modified-Since"] = str(
                    first_sub.get("last_modified") or ""
                )

            try:
                response = await client.get(url, headers=request_headers)
                if response.status_code == 304:
                    continue
                response.raise_for_status()
            except Exception as exc:
                logger.error("Network error checking feed %s: %s", url, exc)
                continue

            try:
                feed = await loop.run_in_executor(None, feedparser.parse, response.content)
            except Exception as exc:
                logger.error("Failed to parse feed %s: %s", url, exc)
                continue

            if getattr(feed, "bozo", False) and getattr(feed, "bozo_exception", None):
                logger.warning("Feed bozo %s: %s", url, feed.bozo_exception)
            if not getattr(feed, "entries", None):
                continue

            current_ids: list[str] = []
            for entry in list(feed.entries)[:20]:
                entry_id = _get_entry_hash(entry, url)
                legacy_id = str(getattr(entry, "id", getattr(entry, "link", None)) or "")
                if entry_id and entry_id not in current_ids:
                    current_ids.append(entry_id)
                if legacy_id and legacy_id not in current_ids:
                    current_ids.append(legacy_id)
            new_hash_str = ",".join(current_ids[:30])
            response_etag = str(
                response.headers.get("etag") or getattr(feed, "etag", "") or ""
            )
            response_modified = str(
                response.headers.get("last-modified")
                or getattr(feed, "modified", "")
                or ""
            )

            for sub in subs:
                last_hashes = [
                    token.strip()
                    for token in str(sub.get("last_entry_hash") or "").split(",")
                    if token.strip()
                ]
                entries_to_push: list[object] = []
                if not last_hashes:
                    if feed.entries:
                        entries_to_push.append(feed.entries[0])
                else:
                    for entry in feed.entries:
                        entry_id = _get_entry_hash(entry, url)
                        legacy_id = str(
                            getattr(entry, "id", getattr(entry, "link", None)) or ""
                        )
                        if len(last_hashes) == 1 and (
                            entry_id in last_hashes or legacy_id in last_hashes
                        ):
                            break
                        if (
                            entry_id and entry_id not in last_hashes
                        ) and legacy_id not in last_hashes:
                            entries_to_push.append(entry)
                        if len(entries_to_push) >= 3:
                            break

                for latest_entry in entries_to_push:
                    title = str(
                        getattr(latest_entry, "get", lambda *_: "")("title", "无标题")
                        or "无标题"
                    )
                    link = _resolve_entry_link(latest_entry, fallback_url=url)
                    feed_title = str(
                        feed.feed.get("title", sub.get("title") or "RSS 订阅")
                    )

                    content_field = ""
                    if hasattr(latest_entry, "summary"):
                        content_field = str(latest_entry.summary or "")
                    elif hasattr(latest_entry, "content") and latest_entry.content:
                        content_field = str(latest_entry.content[0].get("value", "") or "")
                    elif hasattr(latest_entry, "description"):
                        content_field = str(latest_entry.description or "")

                    content_clean = re.sub(r"<[^>]+>", "", content_field).strip()
                    summary = (
                        await generate_entry_summary(title, content_clean, link)
                        if content_clean
                        else "暂无摘要"
                    )

                    uid = SINGLE_USER_SCOPE
                    binding_platform = str(
                        rss_delivery_target.get("platform")
                        or sub.get("platform")
                        or "telegram"
                    )
                    binding_chat_id = str(
                        rss_delivery_target.get("chat_id")
                        or sub.get("chat_id")
                        or sub.get("platform_user_id")
                        or ""
                    ).strip()
                    platform = binding_platform
                    key = (platform, uid)
                    user_updates.setdefault(key, []).append(
                        {
                            "subscription_id": int(sub.get("id") or 0),
                            "user_id": uid,
                            "platform": platform,
                            "feed_title": feed_title,
                            "title": title,
                            "summary": summary,
                            "link": link,
                            "last_entry_hash": new_hash_str,
                            "last_etag": response_etag,
                            "last_modified": response_modified,
                            **(
                                {
                                    "resource_binding": {
                                        "platform": binding_platform,
                                        "owner_user_id": uid,
                                        "chat_id": binding_chat_id,
                                    }
                                }
                                if binding_chat_id
                                else {}
                            ),
                        }
                    )
                    pending_updates.append(user_updates[key][-1])

    formatted = ""
    if user_id is not None:
        for (platform, uid), updates in user_updates.items():
            if str(uid) != str(user_id):
                continue
            section = _format_feed_updates(updates)
            if not section:
                continue
            if formatted:
                formatted += "\n\n"
            formatted += section
    return formatted, pending_updates, user_updates


async def _mark_feed_updates_as_read(pending_updates: list[dict[str, object]]) -> None:
    seen: set[int] = set()
    for update in pending_updates:
        sub_id = int(update.get("subscription_id") or 0)
        if sub_id <= 0 or sub_id in seen:
            continue
        seen.add(sub_id)
        try:
            await update_feed_subscription_state(
                SINGLE_USER_SCOPE,
                sub_id,
                last_entry_hash=str(update.get("last_entry_hash") or ""),
                last_etag=str(update.get("last_etag") or ""),
                last_modified=str(update.get("last_modified") or ""),
            )
        except Exception as exc:
            logger.error(
                "Failed to update feed subscription state for %s: %s",
                sub_id,
                exc,
            )


async def _send_feed_updates(
    user_updates_map: dict[tuple[str, str], list[dict[str, object]]],
) -> int:
    from core.scheduler import (
        _remember_proactive_delivery_target,
        _resolve_proactive_delivery_target,
        send_via_adapter,
    )

    sent_count = 0
    delivered_updates: list[dict[str, object]] = []
    for (platform, uid), updates in user_updates_map.items():
        target_metadata = updates[0] if updates else None
        target_platform, target_chat_id = await _resolve_proactive_delivery_target(
            uid, platform, metadata=target_metadata
        )
        if not target_platform or not target_chat_id:
            logger.warning(
                "Feed push skipped: no delivery target for user=%s on %s",
                uid,
                platform,
            )
            continue

        items = []
        for update in updates:
            items.append(
                f"🔹 **{update['feed_title']}**\n"
                f"[{update['title']}]({update['link']})\n"
                f"📝 {update['summary']}\n\n"
            )

        batches = _split_message_batches(
            f"📢 **RSS 更新 ({len(updates)} 条)**\n\n",
            items,
        )
        try:
            delivery_ok = True
            for idx, batch in enumerate(batches, start=1):
                header = batch
                if idx > 1 and batch.startswith("📢 **RSS 更新"):
                    header = batch.replace("📢 **RSS 更新", "📢 **RSS 更新 (续)", 1)
                delivery_ok = await send_via_adapter(
                    chat_id=target_chat_id,
                    text=header,
                    platform=target_platform,
                    user_id=uid,
                    record_history=True,
                )
                if not delivery_ok:
                    break
            if not delivery_ok:
                logger.warning(
                    "Feed push failed: adapter delivery unsuccessful for user=%s on %s",
                    uid,
                    target_platform,
                )
                continue
            await _remember_proactive_delivery_target(uid, target_platform, target_chat_id)
            delivered_updates.extend(updates)
            sent_count += len(batches)
        except Exception as exc:
            logger.error(
                "Failed to send feed updates to %s on %s: %s",
                uid,
                platform,
                exc,
            )

    await _mark_feed_updates_as_read(delivered_updates)
    return sent_count


async def check_feed_updates_job() -> None:
    logger.info("Checking RSS feed updates...")
    if _rss_check_lock.locked():
        logger.info("Subscription check already in progress, waiting for lock...")
    async with _rss_check_lock:
        subscriptions = await list_feed_subscriptions()
        if not subscriptions:
            logger.info("No feed subscriptions found.")
            return
        _, _, user_updates_map = await _fetch_feed_updates(subscriptions=subscriptions)
        if user_updates_map:
            await _send_feed_updates(user_updates_map)


async def trigger_manual_rss_check(
    user_id: int | str,
    *,
    suppress_busy_message: bool = False,
) -> str:
    if _rss_check_lock.locked():
        if suppress_busy_message:
            return ""
        return "⚠️ 正在进行定时更新检查，请稍后再试。"

    async with _rss_check_lock:
        feed_text, feed_pending, _ = await _fetch_feed_updates(user_id=user_id)
        if feed_pending:
            await _mark_feed_updates_as_read(feed_pending)
        return feed_text


def register_jobs(scheduler) -> None:
    scheduler.add_job(
        check_feed_updates_job,
        "interval",
        seconds=FEED_CHECK_INTERVAL_SEC,
        next_run_time=datetime.datetime.now() + datetime.timedelta(seconds=30),
        id="skill_rss_subscribe_feed_check",
        replace_existing=True,
    )
    logger.info(
        "Registered rss_subscribe scheduled job: interval=%ss",
        FEED_CHECK_INTERVAL_SEC,
    )


async def fetch_feed_safe(url: str):
    """Safely fetch and parse RSS feed asynchronously."""
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        content = response.content

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, feedparser.parse, content)


async def process_subscribe(ctx: UnifiedContext, url: str) -> dict:
    target = str(url or "").strip()
    if not target.startswith(("http://", "https://")):
        return {"text": "❌ `/rss add` 只接受真实 RSS/Atom 链接。", "ui": {}}

    try:
        feed = await fetch_feed_safe(target)
    except Exception as exc:
        logger.error("Feed fetch failed: %s", exc)
        return {"text": f"❌ 无法连接到 RSS 源: {exc}", "ui": {}}

    title = str(feed.feed.get("title") or "").strip() or target
    platform = str(ctx.message.platform or "telegram").strip() or "telegram"
    await _ensure_default_rss_delivery_target(ctx, ctx.message.user.id)

    try:
        created = await create_subscription(
            ctx.message.user.id,
            {
                "title": title,
                "platform": platform,
                "feed_url": target,
            },
        )
    except Exception as exc:
        if "already exists" in str(exc):
            return {"text": "⚠️ 您已经订阅过这个 RSS 源了。", "ui": {}}
        return {"text": f"❌ 订阅失败: {exc}", "ui": {}}

    try:
        await increment_stat(int(ctx.message.user.id), "subscriptions_added")
    except Exception:
        pass

    return {
        "text": (
            "✅ **RSS 订阅已创建**\n\n"
            f"ID：`{created['id']}`\n"
            f"标题：{created['title']}\n"
            "Bot 将每 30 分钟检查一次更新。"
        ),
        "ui": {},
    }


async def list_subs_command(
    ctx: UnifiedContext,
    *,
    include_menu_nav: bool = False,
) -> dict:
    subs = await list_subscriptions(ctx.message.user.id)
    if not subs:
        return {
            "text": "📭 您当前没有任何 RSS 订阅。",
            "ui": _rss_menu_ui() if include_menu_nav else {},
        }

    msg = "📋 **您的订阅列表**\n\n"
    for sub in subs:
        msg += (
            f"• `#{sub['id']}` [RSS] **{sub['title']}**\n"
            f"  {sub['feed_url']}\n\n"
        )

    msg += "使用 `/rss remove <订阅ID>` 取消订阅。"

    actions = []
    temp_row = []
    for sub in subs:
        label = f"❌ #{sub['id']}"
        temp_row.append({"text": label, "callback_data": f"unsub_{sub['id']}"})
        if len(temp_row) == 3:
            actions.append(temp_row)
            temp_row = []
    if temp_row:
        actions.append(temp_row)

    if include_menu_nav:
        actions.append(
            [
                {"text": "🔄 立即检查", "callback_data": make_callback(RSS_MENU_NS, "refresh")},
                {"text": "🏠 返回首页", "callback_data": make_callback(RSS_MENU_NS, "home")},
            ]
        )

    return {"text": msg, "ui": {"actions": actions}}


async def refresh_user_subscriptions(ctx: UnifiedContext) -> str:
    user_id = ctx.message.user.id

    if ctx.platform_ctx:
        try:
            await ctx.platform_ctx.bot.send_chat_action(
                chat_id=ctx.message.chat.id, action="typing"
            )
        except Exception:
            pass

    result_text = await trigger_manual_rss_check(user_id)
    if result_text:
        return result_text
    return "✅ 检查完成，当前 RSS 订阅没有新增内容。"


async def show_unsubscribe_menu(
    ctx: UnifiedContext,
    *,
    include_menu_nav: bool = False,
) -> dict:
    subs = await list_subscriptions(ctx.message.user.id)
    if not subs:
        return {
            "text": "📭 您当前没有任何 RSS 订阅。",
            "ui": _rss_menu_ui() if include_menu_nav else {},
        }

    actions = []
    for sub in subs:
        label = f"❌ #{sub['id']} {sub['title']}"
        actions.append([{"text": label[:28], "callback_data": f"unsub_{sub['id']}"}])
    if include_menu_nav:
        actions.append([{"text": "🏠 返回首页", "callback_data": make_callback(RSS_MENU_NS, "home")}])
    actions.append([{"text": "🚫 取消", "callback_data": "unsub_cancel"}])

    return {"text": "📋 **请选择要取消的订阅**：", "ui": {"actions": actions}}


async def remove_subscription_by_target(ctx: UnifiedContext, target: str) -> dict:
    raw = str(target or "").strip()
    if not raw:
        return {"text": "用法: `/rss remove <订阅ID>`", "ui": {}}

    try:
        sub_id = int(raw.lstrip("#"))
    except ValueError:
        return {"text": "❌ `/rss remove` 现在只支持按订阅 ID 删除。", "ui": {}}

    sub = await get_subscription(ctx.message.user.id, sub_id)
    if sub is None:
        return {"text": f"❌ 未找到订阅 `#{sub_id}`。", "ui": {}}
    success = await delete_subscription(ctx.message.user.id, sub_id)
    if not success:
        return {"text": f"❌ 删除失败，订阅 `#{sub_id}` 可能已不存在。", "ui": {}}
    return {
        "text": f"✅ 已取消订阅 `#{sub_id}`：{sub['title']}",
        "ui": {},
    }


async def handle_unsubscribe_callback(ctx: UnifiedContext):
    if not _rss_enabled(ctx):
        await ctx.reply(channel_feature_denied_text("rss"))
        return
    data = ctx.callback_data
    if not data:
        return

    action, _parts = parse_callback(data, RSS_MENU_NS)
    if action:
        await ctx.answer_callback()
        if action == "home":
            payload = await show_rss_menu(ctx)
        elif action == "list":
            payload = await list_subs_command(ctx, include_menu_nav=True)
        elif action == "refresh":
            payload = {"text": await refresh_user_subscriptions(ctx), "ui": _rss_menu_ui()}
        elif action == "bind":
            target = _current_delivery_target(ctx)
            updated = await set_feature_delivery_target(
                ctx.callback_user_id or ctx.message.user.id,
                "rss",
                target["platform"],
                target["chat_id"],
            )
            menu = await show_rss_menu(ctx)
            payload = {
                "text": (
                    "✅ 已把 RSS 推送渠道切换到当前聊天 "
                    f"`{_format_delivery_target(updated)}`。\n\n{menu['text']}"
                ),
                "ui": menu.get("ui"),
            }
        elif action == "addhelp":
            payload = _rss_add_help_response()
        elif action == "remove":
            payload = await show_unsubscribe_menu(ctx, include_menu_nav=True)
        else:
            payload = {"text": "❌ 未知操作。", "ui": _rss_menu_ui()}
        await ctx.edit_message(ctx.message.id, payload["text"], ui=payload.get("ui"))
        return

    await ctx.answer_callback()

    if data == "unsub_cancel":
        return "👌 已取消操作。"

    try:
        sub_id = int(str(data).replace("unsub_", ""))
    except ValueError:
        return "❌ 无效的操作。"

    success = await delete_subscription(ctx.callback_user_id, sub_id)
    if success:
        payload = await list_subs_command(ctx, include_menu_nav=True)
        await ctx.edit_message(
            ctx.message.id,
            f"✅ 已取消订阅 `#{sub_id}`。\n\n{payload['text']}",
            ui=payload.get("ui"),
        )
        return None
    return "❌ 取消失败，订阅可能已不存在。"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RSS subscribe skill CLI bridge.")
    add_common_arguments(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List subscriptions")

    add_parser = subparsers.add_parser("add", help="Add an RSS feed subscription")
    add_parser.add_argument("target", help="RSS URL")

    remove_parser = subparsers.add_parser("remove", help="Remove a subscription")
    remove_parser.add_argument("target", help="Subscription ID")

    subparsers.add_parser("refresh", help="Refresh subscriptions now")
    return parser


def _params_from_args(args: argparse.Namespace) -> dict:
    command = str(args.command or "").strip().lower()
    if command == "list":
        return merge_params(args, {"action": "list"})
    if command == "add":
        return merge_params(args, {"action": "add", "url": str(args.target or "").strip()})
    if command == "remove":
        return merge_params(
            args, {"action": "remove", "target": str(args.target or "").strip()}
        )
    if command == "refresh":
        return merge_params(args, {"action": "refresh"})
    raise SystemExit(f"unsupported command: {command}")


from core.extension_base import SkillExtension


class RssSubscribeSkillExtension(SkillExtension):
    name = "rss_subscribe_extension"
    skill_name = "rss_subscribe"

    def register(self, runtime) -> None:
        register_handlers(runtime.adapter_manager)
        register_jobs(runtime.scheduler)


if __name__ == "__main__":
    run_execute_cli(
        _build_parser(),
        _params_from_args,
        execute,
    )
