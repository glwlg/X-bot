"""
任务调度模块 - 处理定时提醒
"""

import asyncio
import logging
import datetime
import html
import re
import dateutil.parser
import feedparser
import hashlib
import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from core.background_delivery import push_background_text
from core.heartbeat_store import heartbeat_store
from core.platform.registry import adapter_manager
from core.platform.models import UnifiedContext
from core.proactive_delivery import resolve_proactive_target
from core.state_paths import SINGLE_USER_SCOPE
from shared.contracts.proactive_delivery_target import normalize_proactive_platform

from core.state_store import (
    add_reminder,
    delete_reminder,
    get_pending_reminders,
    get_user_watchlist,
    get_all_watchlist_users,
    list_feed_subscriptions,
    list_subscriptions,
    update_feed_subscription_state,
)
from core.state_store import get_all_active_tasks, save_message, get_latest_session_id

logger = logging.getLogger(__name__)

# Global Scheduler Instance
scheduler = AsyncIOScheduler()


async def _resolve_proactive_delivery_target(
    user_id: int | str,
    platform: str,
    metadata: dict[str, object] | None = None,
) -> tuple[str, str]:
    return await resolve_proactive_target(
        owner_user_id=str(user_id or "").strip(),
        platform=platform,
        metadata=metadata,
    )


async def _remember_proactive_delivery_target(
    user_id: int | str,
    platform: str,
    chat_id: str,
    session_id: str = "",
) -> None:
    target_platform = normalize_proactive_platform(platform)
    target_chat_id = str(chat_id or "").strip()
    if not target_platform or not target_chat_id:
        return
    try:
        await heartbeat_store.set_delivery_target(
            str(user_id or "").strip(),
            target_platform,
            target_chat_id,
            session_id=session_id,
        )
    except Exception:
        logger.debug("Failed to remember proactive delivery target.", exc_info=True)


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
        if not url.startswith("http"):
            continue
        return url

    return primary or fallback


def _get_entry_hash(entry: object, _feed_url: str) -> str:
    """基于内容为 RSS 条目生成稳定的特征 hash"""
    title = str(getattr(entry, "get", lambda *x: "")("title", "") or "")
    if title:
        return hashlib.md5(title.encode("utf-8")).hexdigest()

    # Fallback 到 ID 获取 link
    fallback_id = str(getattr(entry, "id", getattr(entry, "link", None)) or "")
    if fallback_id:
        return hashlib.md5(fallback_id.encode("utf-8")).hexdigest()
    return ""


async def save_push_message_to_db(user_id: int, message: str):
    """Utility to save pushed messages to chat history"""
    try:
        session_id = await get_latest_session_id(user_id)
        await save_message(user_id, "model", message, session_id)
    except Exception as e:
        logger.error(f"Failed to save push message for {user_id}: {e}")


async def send_via_adapter(
    chat_id: int | str,
    text: str,
    platform: str = "telegram",
    parse_mode: str = "Markdown",
    user_id: int | str = "",
    session_id: str = "",
    record_history: bool = False,
    **kwargs,
):
    """Helper to send message via available adapters"""
    _ = (parse_mode, kwargs)
    push_kwargs = {
        "platform": str(platform or "telegram"),
        "chat_id": str(chat_id or ""),
        "text": str(text or ""),
        "filename_prefix": "background",
    }
    if record_history and str(user_id or "").strip():
        push_kwargs.update(
            {
                "record_history": True,
                "history_user_id": str(user_id or "").strip(),
                "history_session_id": str(session_id or "").strip(),
            }
        )
    ok = await push_background_text(
        **push_kwargs,
    )
    if not ok:
        logger.warning("Background push failed platform=%s chat=%s", platform, chat_id)
    return bool(ok)


async def send_reminder_job(
    reminder_id: int,
    user_id: int,
    chat_id: int,
    message: str,
    platform: str = "telegram",
):
    """发送提醒的工作任务"""
    logger.info(f"Triggering reminder {reminder_id} for chat {chat_id} on {platform}")

    try:
        await send_via_adapter(
            chat_id=chat_id,
            text=f"⏰ **提醒**\n\n{message}",
            platform=platform,
            user_id=user_id,
            record_history=True,
        )
    except Exception as e:
        logger.error(f"Failed to send reminder {reminder_id}: {e}")
    finally:
        await delete_reminder(reminder_id, user_id=user_id)


async def schedule_reminder(
    user_id: int,
    chat_id: int,
    message: str,
    trigger_time: datetime.datetime,
    platform: str = "telegram",
) -> bool:
    """安排一个新的提醒任务"""
    now = datetime.datetime.now().astimezone()

    # Update: If trigger_time is naiive, make it aware (local)
    if trigger_time.tzinfo is None:
        trigger_time = trigger_time.replace(tzinfo=now.tzinfo)

    # 落盘到文件存储
    reminder_id = await add_reminder(
        user_id, chat_id, message, trigger_time.isoformat(), platform=platform
    )

    # 加入 Scheduler
    scheduler.add_job(
        send_reminder_job,
        "date",
        run_date=trigger_time,
        args=[reminder_id, user_id, chat_id, message, platform],
        id=f"reminder_{reminder_id}",
        replace_existing=True,
    )
    return True


async def load_jobs_from_db():
    """从文件存储加载未执行的提醒任务（Bot 启动时调用）"""
    logger.info("Loading pending reminders from filesystem store...")
    reminders = await get_pending_reminders()

    count = 0
    now = datetime.datetime.now().astimezone()

    for row in reminders:
        reminder_id = row["id"]
        trigger_time_str = row["trigger_time"]
        platform = row.get("platform", "telegram")

        try:
            # 解析时间
            trigger_time = dateutil.parser.isoparse(trigger_time_str)

            # 确保此时区意识到 (aware)
            if trigger_time.tzinfo is None:
                trigger_time = trigger_time.replace(tzinfo=now.tzinfo)

            # 如果错过了时间，稍微延迟一点立即执行
            run_time = trigger_time
            delay = (trigger_time - now).total_seconds()
            if delay < 0:
                run_time = now + datetime.timedelta(seconds=5)

            scheduler.add_job(
                send_reminder_job,
                "date",
                run_date=run_time,
                args=[
                    reminder_id,
                    SINGLE_USER_SCOPE,
                    row["chat_id"],
                    row["message"],
                    platform,
                ],
                id=f"reminder_{reminder_id}",
                replace_existing=True,
            )
            count += 1

        except Exception as e:
            logger.error(f"Failed to load reminder {reminder_id}: {e}")

    logger.info(f"Loaded {count} pending reminders.")


async def generate_entry_summary(title: str, content: str, link: str) -> str:
    """使用 AI 生成 RSS 条目摘要"""
    from core.config import get_client_for_model
    from core.model_config import get_current_model
    from services.openai_adapter import generate_text

    # 截断过长内容
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
        summary = await generate_text(
            async_client=client_to_use,
            model=model_to_use,
            contents=prompt,
        )
        return str(summary or "").strip()
    except Exception as e:
        logger.error(f"AI summary generation failed: {e}")
        # 失败时返回原始内容的截断版本
        return content[:200] + "..." if len(content) > 200 else content


FEED_CHECK_INTERVAL_SEC = 30 * 60
_rss_check_lock = asyncio.Lock()


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

    feed_map: dict[str, list[dict[str, object]]] = {}
    for sub in subscriptions:
        url = str(sub.get("feed_url") or "").strip()
        if not url:
            continue
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
            except Exception as e:
                logger.error("Network error checking feed %s: %s", url, e)
                continue

            try:
                feed = await loop.run_in_executor(
                    None, feedparser.parse, response.content
                )
            except Exception as e:
                logger.error("Failed to parse feed %s: %s", url, e)
                continue

            if getattr(feed, "bozo", False) and getattr(feed, "bozo_exception", None):
                logger.warning("Feed bozo %s: %s", url, feed.bozo_exception)
            if not getattr(feed, "entries", None):
                continue

            current_ids: list[str] = []
            for entry in list(feed.entries)[:20]:
                e_id = _get_entry_hash(entry, url)
                legacy_id = str(
                    getattr(entry, "id", getattr(entry, "link", None)) or ""
                )
                if e_id and e_id not in current_ids:
                    current_ids.append(e_id)
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
                    h.strip()
                    for h in str(sub.get("last_entry_hash") or "").split(",")
                    if h.strip()
                ]
                entries_to_push: list[object] = []
                if not last_hashes:
                    if feed.entries:
                        entries_to_push.append(feed.entries[0])
                else:
                    for entry in feed.entries:
                        e_id = _get_entry_hash(entry, url)
                        legacy_id = str(
                            getattr(entry, "id", getattr(entry, "link", None)) or ""
                        )
                        if len(last_hashes) == 1 and (
                            e_id in last_hashes or legacy_id in last_hashes
                        ):
                            break
                        if (
                            e_id and e_id not in last_hashes
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
                        content_field = str(
                            latest_entry.content[0].get("value", "") or ""
                        )
                    elif hasattr(latest_entry, "description"):
                        content_field = str(latest_entry.description or "")

                    content_clean = re.sub(r"<[^>]+>", "", content_field).strip()
                    summary = (
                        await generate_entry_summary(title, content_clean, link)
                        if content_clean
                        else "暂无摘要"
                    )

                    uid = SINGLE_USER_SCOPE
                    plat = str(sub.get("platform") or "telegram")
                    key = (plat, uid)
                    user_updates.setdefault(key, []).append(
                        {
                            "subscription_id": int(sub.get("id") or 0),
                            "user_id": uid,
                            "platform": plat,
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
                                        "platform": plat,
                                        "owner_user_id": uid,
                                        **{
                                            binding_key: str(
                                                sub.get(binding_key) or ""
                                            ).strip()
                                            for binding_key in (
                                                "chat_id",
                                                "platform_user_id",
                                            )
                                            if str(sub.get(binding_key) or "").strip()
                                        },
                                    }
                                }
                                if any(
                                    str(sub.get(binding_key) or "").strip()
                                    for binding_key in ("chat_id", "platform_user_id")
                                )
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
        except Exception as e:
            logger.error(
                "Failed to update feed subscription state for %s: %s", sub_id, e
            )


async def _send_feed_updates(
    user_updates_map: dict[tuple[str, str], list[dict[str, object]]],
) -> int:
    sent_count = 0
    delivered_updates: list[dict[str, object]] = []
    for (platform, uid), updates in user_updates_map.items():
        target_metadata = updates[0] if updates else None
        target_platform, target_chat_id = await _resolve_proactive_delivery_target(
            uid, platform, metadata=target_metadata
        )
        if not target_platform or not target_chat_id:
            logger.warning(
                "Feed push skipped: no delivery target for user=%s on %s", uid, platform
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
            await _remember_proactive_delivery_target(
                uid, target_platform, target_chat_id
            )
            delivered_updates.extend(updates)
            sent_count += len(batches)
        except Exception as e:
            logger.error(
                "Failed to send feed updates to %s on %s: %s", uid, platform, e
            )

    await _mark_feed_updates_as_read(delivered_updates)
    return sent_count


async def check_feed_updates_job():
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
    user_id: int,
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


def start_rss_scheduler():
    """启动 RSS 订阅定时任务"""
    now = datetime.datetime.now()
    scheduler.add_job(
        check_feed_updates_job,
        "interval",
        seconds=FEED_CHECK_INTERVAL_SEC,
        next_run_time=now + datetime.timedelta(seconds=30),
        id="feed_check",
        replace_existing=True,
    )
    logger.info("RSS scheduler started: interval=%ss", FEED_CHECK_INTERVAL_SEC)


# --- 股票盯盘推送 ---


def is_trading_time() -> bool:
    """
    判断当前是否为 A 股交易时段
    - 周一至周五
    - 上午 9:30-11:30，下午 13:00-15:00
    """
    now = datetime.datetime.now()

    # 周末不交易 (0=周一, 6=周日)
    if now.weekday() >= 5:
        return False

    current_time = now.time()

    # 上午交易时段: 9:30 - 11:30
    morning_start = datetime.time(9, 30)
    morning_end = datetime.time(11, 30)

    # 下午交易时段: 13:00 - 15:00
    afternoon_start = datetime.time(13, 0)
    afternoon_end = datetime.time(15, 0)

    return (
        morning_start <= current_time <= morning_end
        or afternoon_start <= current_time <= afternoon_end
    )


async def stock_push_job():
    """每 10 分钟推送股票行情"""
    if not is_trading_time():
        logger.debug("Not trading time, skipping stock push")
        return

    logger.info("Starting stock push job...")

    # 延迟导入避免循环引用
    from services.stock_service import fetch_stock_quotes, format_stock_message

    try:
        # 获取所有有自选股的用户 (returns list of (user_id, platform))
        users_with_platform = await get_all_watchlist_users()

        if not users_with_platform:
            logger.info("No users with watchlist, skipping")
            return

        for user_id, platform in users_with_platform:
            try:
                # 获取用户自选股 (filtered by platform)
                watchlist = await get_user_watchlist(user_id, platform=platform)
                if not watchlist:
                    continue

                # 提取股票代码
                stock_codes = [item["stock_code"] for item in watchlist]

                # 批量获取行情
                quotes = await fetch_stock_quotes(stock_codes)

                if not quotes:
                    continue

                # 格式化消息
                message = format_stock_message(quotes)

                (
                    target_platform,
                    target_chat_id,
                ) = await _resolve_proactive_delivery_target(
                    user_id,
                    platform,
                )
                if not target_platform or not target_chat_id:
                    logger.warning(
                        "Stock push skipped: no delivery target for user=%s on %s",
                        user_id,
                        platform,
                    )
                    continue

                # 推送给用户 (via specific platform)
                await send_via_adapter(
                    chat_id=target_chat_id,
                    text=message,
                    platform=target_platform,
                    user_id=user_id,
                    record_history=True,
                )
                await _remember_proactive_delivery_target(
                    user_id,
                    target_platform,
                    target_chat_id,
                )
                logger.info(
                    "Sent stock quotes to user %s on %s",
                    user_id,
                    target_platform,
                )

            except Exception as e:
                logger.error(
                    f"Failed to send stock quotes to {user_id} on {platform}: {e}"
                )

    except Exception as e:
        logger.error(f"Stock push job error: {e}")


async def trigger_manual_stock_check(user_id: int) -> str:
    """
    [Tool Logic] 手动触发特定用户的自选股行情刷新
    返回格式化后的行情文本
    """
    from services.stock_service import fetch_stock_quotes, format_stock_message

    try:
        # 获取用户自选股
        watchlist = await get_user_watchlist(user_id)
        if not watchlist:
            return ""  # Empty watchlist

        # 提取股票代码
        stock_codes = [item["stock_code"] for item in watchlist]

        # 批量获取行情
        quotes = await fetch_stock_quotes(stock_codes)

        if not quotes:
            return "❌ 无法获取行情数据，请稍后重试。"

        # 格式化消息
        message = format_stock_message(quotes)
        return message

    except Exception as e:
        logger.error(f"Manual stock check error for {user_id}: {e}")
        return f"❌ 刷新失败: {str(e)}"


def start_stock_scheduler():
    """启动股票推送定时任务"""
    interval = 10 * 60  # 10 分钟

    scheduler.add_job(
        stock_push_job,
        "interval",
        seconds=interval,
        next_run_time=datetime.datetime.now() + datetime.timedelta(seconds=5),
        id="stock_push",
        replace_existing=True,
    )
    logger.info(f"Stock scheduler started, interval={interval}s")


# --- 动态 Skill 调度 ---


async def run_skill_cron_job(
    instruction: str,
    user_id: int | str = 0,
    platform: str = "telegram",
    need_push: bool = False,
):
    """
    通用 Skill 定时任务执行器
    """
    user_id_text = str(user_id or "").strip()
    if not user_id_text:
        user_id_text = "0"

    logger.info(
        f"[Cron] Executing scheduled skill: '{instruction}' for user {user_id_text} on {platform}"
    )

    try:
        from core.agent_input import MAX_INLINE_IMAGE_INPUTS, build_agent_message_history
        from core.platform.models import UnifiedMessage, User, Chat, MessageType
        from core.agent_orchestrator import agent_orchestrator

        mock_user = User(id=user_id_text, username="Cron User", is_bot=False)
        mock_chat = Chat(id=user_id_text, type="private")
        mock_message = UnifiedMessage(
            id=f"cron-{int(datetime.datetime.now().timestamp())}",
            platform=platform,
            user=mock_user,
            chat=mock_chat,
            text=instruction,
            date=datetime.datetime.now(),
            type=MessageType.TEXT,
        )

        adapter = None
        try:
            adapter = adapter_manager.get_adapter(platform)
        except Exception:
            adapter = None

        ctx = UnifiedContext(
            message=mock_message,
            platform_ctx=None,
            _adapter=adapter,
            user=mock_user,
        )

        if not instruction:
            instruction = "Execute scheduled maintenance/run_cron task."

        cron_task_id = f"cron-{int(datetime.datetime.now().timestamp())}"

        final_output = []

        prompt_text = (
            f"[CRON TASK id={cron_task_id}]\n"
            f"source=cron\n"
            f"【系统级别最高指令】：你当前正在“执行”一个已被触发的系统定时任务！\n"
            f"请从以下目标描述中提取需要真实执行的查询、分析等动作并**立刻执行它**。\n"
            f"如果目标描述里带有“每天/每小时/定时”等字眼，请直接忽略这些时间修饰词，只执行里面提到的查天气、看新闻等实际动作！\n"
            f"**绝对禁止**调用 scheduler_manager 去再次添加、创建新的定时任务（那会导致无限套娃循环）！\n\n"
            f"目标任务描述：{instruction}"
        )
        prepared_input = await build_agent_message_history(
            ctx,
            user_message=prompt_text,
            inline_input_source_texts=[instruction],
            strip_refs_from_user_message=False,
            max_inline_inputs=MAX_INLINE_IMAGE_INPUTS,
        )

        if prepared_input.detected_refs and not prepared_input.has_inline_inputs:
            full_response = (
                "❌ 检测到图片链接或本地图片路径，但没有成功加载任何图片。请检查链接或路径后重试。"
            )
        else:
            message_history = list(prepared_input.message_history)

            if prepared_input.truncated_inline_count:
                final_output.append(
                    f"⚠️ 检测到超过 {MAX_INLINE_IMAGE_INPUTS} 张图片，本次仅使用前 {MAX_INLINE_IMAGE_INPUTS} 张。\n\n"
                )
            if prepared_input.errors and prepared_input.has_inline_inputs:
                final_output.append(
                    f"⚠️ 有 {len(prepared_input.errors)} 张图片加载失败，先按成功加载的图片继续分析。\n\n"
                )

            # Execute via Agent Brain
            async for chunk in agent_orchestrator.handle_message(ctx, message_history):
                if chunk and chunk.strip():
                    final_output.append(chunk)

            full_response = "".join(final_output).strip()
        # Push Notification Logic
        if need_push and user_id_text not in {"", "0"}:
            if full_response:
                (
                    target_platform,
                    target_chat_id,
                ) = await _resolve_proactive_delivery_target(
                    user_id_text,
                    platform,
                )
                if not target_platform or not target_chat_id:
                    logger.warning(
                        "[Cron] Push skipped: no delivery target for user=%s on %s",
                        user_id_text,
                        platform,
                    )
                else:
                    logger.info(
                        f"[Cron] Pushing result to {user_id_text} on {target_platform}"
                    )
                    await send_via_adapter(
                        chat_id=target_chat_id,
                        text=f"⏰ **定时任务执行报告 ({instruction})**\n\n{full_response}",
                        platform=target_platform,
                        user_id=user_id_text,
                        record_history=True,
                    )
                    await _remember_proactive_delivery_target(
                        user_id_text,
                        target_platform,
                        target_chat_id,
                    )
            else:
                logger.info(f"[Cron] No output to push for {instruction}")

    except Exception as e:
        logger.error(f"[Cron] Failed to run skill {instruction}: {e}", exc_info=True)


async def reload_scheduler_jobs():
    """
    重新加载文件存储中的定时任务 (全量刷新)
    """
    logger.info("Reloading scheduler jobs from filesystem store...")

    # 1. Clear existing dynamic jobs to handle deletions/updates
    # We identify them by ID prefix "cron_db_"
    # Note: scheduler.get_jobs() returns a list
    start_time = datetime.datetime.now()
    removed_count = 0
    for job in scheduler.get_jobs():
        if job.id.startswith("cron_db_"):
            try:
                job.remove()
                removed_count += 1
            except Exception:
                pass

    if removed_count > 0:
        logger.info(f"Removed {removed_count} existing dynamic jobs.")

    # 2. Load from store
    tasks = await get_all_active_tasks()
    count = 0
    for task in tasks:
        task_id = task["id"]
        crontab = task["crontab"]
        instruction = task["instruction"]
        user_id = SINGLE_USER_SCOPE
        platform = task.get("platform", "telegram")
        # SQLite stores boolean as 0/1 usually, ensures compat
        need_push = bool(task.get("need_push", True))

        try:
            parts = crontab.split()
            if len(parts) == 5:
                trigger = CronTrigger(
                    minute=parts[0],
                    hour=parts[1],
                    day=parts[2],
                    month=parts[3],
                    day_of_week=parts[4],
                )

                scheduler.add_job(
                    run_skill_cron_job,
                    trigger,
                    id=f"cron_db_{task_id}",
                    args=[instruction, user_id, platform, need_push],
                    replace_existing=True,
                )
                count += 1
            else:
                logger.warning(
                    f"Invalid crontab format for task {instruction}: {crontab}"
                )
        except Exception as e:
            logger.error(f"Failed to register cron for task {instruction}: {e}")

    logger.info(
        f"Reloaded {count} jobs from filesystem store in {(datetime.datetime.now() - start_time).total_seconds()}s."
    )


def start_dynamic_skill_scheduler():
    """
    启动动态 Skill 调度器 (Initial Load)
    """
    scheduler.add_job(
        reload_scheduler_jobs,
        "date",
        run_date=datetime.datetime.now() + datetime.timedelta(seconds=5),
        misfire_grace_time=30,
    )
