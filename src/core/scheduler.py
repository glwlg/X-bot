"""
任务调度模块 - 处理定时提醒
"""

import asyncio
import logging
import datetime
import html
import re
import urllib.parse
import dateutil.parser
import feedparser
import hashlib
import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from core.platform.registry import adapter_manager
from core.platform.models import UnifiedContext

from core.state_store import (
    add_reminder,
    delete_reminder,
    get_pending_reminders,
    get_all_subscriptions,
    update_subscription_status,
    get_user_watchlist,
    get_all_watchlist_users,
)
from core.state_store import get_all_active_tasks, save_message, get_latest_session_id

logger = logging.getLogger(__name__)

# Global Scheduler Instance
scheduler = AsyncIOScheduler()


def _is_google_rss_redirect(url: str) -> bool:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    return host.endswith("news.google.com") and "/rss/articles/" in path


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
    if primary:
        candidates.append(primary)

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

    for url in normalized:
        if not url.startswith("http"):
            continue
        if _is_google_rss_redirect(url):
            continue
        return url

    return primary or fallback


def _get_entry_hash(entry: object, feed_url: str) -> str:
    """基于内容为 RSS 条目生成稳定的特征 hash"""
    title = str(getattr(entry, "get", lambda *x: "")("title", "") or "")
    if title:
        raw_title = title
        if _is_google_rss_redirect(feed_url) or "news.google.com" in str(feed_url):
            # 对于 Google News, 标题通常会带有 " - 来源" 后缀，导致同义新闻聚合更新时 ID 虽然改变，标题大体一致
            raw_title = re.sub(r"\s+-\s+[^-]+$", "", title).strip()

        # 只取前 100 个字符进行哈希，防止微调，但对全量字符哈希也是足够安全并且确定的
        return hashlib.md5(raw_title.encode("utf-8")).hexdigest()

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
    **kwargs,
):
    """Helper to send message via available adapters"""

    # 尝试获取对应平台的 Adapter
    try:
        adapter = adapter_manager.get_adapter(platform)
    except Exception:
        adapter = None

    if adapter:
        try:
            send_message = getattr(adapter, "send_message", None)
            if callable(send_message):
                await send_message(chat_id=chat_id, text=text)
            else:
                bot = getattr(adapter, "bot", None)
                if platform == "telegram" and bot is not None:
                    from platforms.telegram.formatter import markdown_to_telegram_html

                    await bot.send_message(
                        chat_id=chat_id,
                        text=markdown_to_telegram_html(text),
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                        **kwargs,
                    )
                else:
                    logger.warning(f"Unknown platform or no send method: {platform}")
            return
        except Exception as e:
            logger.error(f"{platform} send failed: {e}")
    else:
        logger.warning(f"No adapter found for platform: {platform}")


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
            chat_id=chat_id, text=f"⏰ **提醒**\n\n{message}", platform=platform
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
                    row["user_id"],
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


# 全局锁，防止定时任务和手动触发撞车
_rss_check_lock = asyncio.Lock()


async def fetch_formatted_rss_updates(
    user_id: int = None, subscriptions: list = None
) -> tuple[str, list, dict]:
    """
    获取并格式化 RSS 更新，但不发送。
    返回: (formatted_message, pending_updates_list, user_updates_map)
    user_updates_map: dict[(platform, user_id)] -> list
    """
    # 1. 获取订阅 (如果没有传入)
    if not subscriptions:
        if user_id:
            from core.state_store import get_user_subscriptions

            subscriptions = await get_user_subscriptions(user_id)
        else:
            subscriptions = await get_all_subscriptions()

    if not subscriptions:
        return "", [], {}

    # 2. 按 feed_url 分组
    feed_map = {}
    for sub in subscriptions:
        url = sub["feed_url"]
        if url not in feed_map:
            feed_map[url] = []
        feed_map[url].append(sub)

    user_updates = {}  # (platform, user_id) -> list of updates
    all_pending_updates = []

    # 3. 抓取逻辑
    loop = asyncio.get_running_loop()

    # Shared client for all fetches
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        for url, subs in feed_map.items():
            try:
                # 1. Async Fetch
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    content = response.content
                except Exception as e:
                    logger.error(f"Network error checking feed {url}: {e}")
                    continue

                # 2. Threaded Parse
                feed = await loop.run_in_executor(None, feedparser.parse, content)

                if feed.bozo and feed.bozo_exception:
                    # Some feeds result in bozo but have entries, log but continue
                    logger.warning(f"Feed bozo {url}: {feed.bozo_exception}")

                if not feed.entries:
                    continue

                current_ids = []
                for entry in feed.entries[:20]:
                    e_id = _get_entry_hash(entry, url)
                    legacy_id = str(
                        getattr(entry, "id", getattr(entry, "link", None)) or ""
                    )

                    if e_id and e_id not in current_ids:
                        current_ids.append(e_id)

                    # 同时保留原始的 ID (防止一次性将所有的老的新闻全推过来)
                    if legacy_id and legacy_id not in current_ids:
                        current_ids.append(legacy_id)

                # 保留前 30 个哈希防止过长
                new_hash_str = ",".join(current_ids[:30])

                for sub in subs:
                    last_hash_str = str(sub.get("last_entry_hash") or "")
                    last_hashes = [
                        h.strip() for h in last_hash_str.split(",") if h.strip()
                    ]

                    entries_to_push = []
                    if not last_hashes:
                        # 新订阅，只取最新的一条推送
                        if feed.entries:
                            entries_to_push.append(feed.entries[0])
                    else:
                        for entry in feed.entries:
                            e_id = _get_entry_hash(entry, url)
                            legacy_id = str(
                                getattr(entry, "id", getattr(entry, "link", None)) or ""
                            )

                            # 防止老旧系统由于 hash 变换导致全量推送，如果在上一次 hashes 中存在老 legacy_id 或是我们计算的等价的 e_id，都要进行拦截
                            if len(last_hashes) == 1 and (
                                e_id in last_hashes or legacy_id in last_hashes
                            ):
                                break

                            if (e_id and e_id not in last_hashes) and (
                                legacy_id not in last_hashes
                            ):
                                entries_to_push.append(entry)

                            if len(entries_to_push) >= 3:  # 每次最多推 3 条
                                break

                    for latest_entry in entries_to_push:
                        title = str(
                            getattr(latest_entry, "get", lambda *_: "")(
                                "title", "无标题"
                            )
                            or "无标题"
                        )
                        link = _resolve_entry_link(latest_entry, fallback_url=url)
                        feed_title = feed.feed.get("title", "RSS 订阅")

                        # Content summary logic...
                        content_field = ""
                        if hasattr(latest_entry, "summary"):
                            content_field = latest_entry.summary
                        elif hasattr(latest_entry, "content") and latest_entry.content:
                            content_field = latest_entry.content[0].get("value", "")
                        elif hasattr(latest_entry, "description"):
                            content_field = latest_entry.description

                        content_clean = re.sub(r"<[^>]+>", "", content_field).strip()

                        if content_clean:
                            summary = await generate_entry_summary(
                                title, content_clean, link
                            )
                        else:
                            summary = "暂无摘要"

                        uid = sub["user_id"]
                        plat = sub.get("platform", "telegram")
                        key = (plat, uid)

                        if key not in user_updates:
                            user_updates[key] = []

                        update_item = {
                            "feed_title": feed_title,
                            "title": title,
                            "summary": summary,
                            "link": link,
                            "user_id": uid,
                            "feed_url": sub["feed_url"],
                            "entry_id": new_hash_str,
                            "etag": getattr(feed, "etag", None),
                            "modified": getattr(feed, "modified", None),
                        }

                        user_updates[key].append(update_item)
                        all_pending_updates.append(update_item)

            except Exception as e:
                logger.error(f"Error checking feed {url}: {e}")

    # 4. 格式化输出 (按用户汇总)
    final_output = ""
    # 如果是指定用户 (Tool 场景)，生成一个大的文本块
    # 注意：Tool 场景通常只针对单一平台 (Telegram) 或者需要适配
    if user_id:
        for key, updates in user_updates.items():
            if key[1] == user_id:
                final_output += (
                    f"📢 **RSS 订阅日报 ({len(updates)} 条更新) [via {key[0]}]**\n\n"
                )
                for update in updates:
                    final_output += (
                        f"🔹 **{update['feed_title']}**\n"
                        f"[{update['title']}]({update['link']})\n"
                        f"📝 {update['summary']}\n\n"
                    )

    return final_output, all_pending_updates, user_updates


async def mark_updates_as_read(pending_updates: list):
    """更新订阅状态"""
    for update in pending_updates:
        try:
            await update_subscription_status(
                update["user_id"],
                update["feed_url"],
                update["entry_id"],
                update["etag"],
                update["modified"],
            )
        except Exception as e:
            logger.error(
                f"Failed to update subscription status for {update.get('feed_url')}: {e}"
            )


async def check_and_send_rss_updates(subscriptions: list):
    """
    [定时任务逻辑] 检查并直接发送 RSS 更新 (带锁)
    """
    if _rss_check_lock.locked():
        logger.info("RSS check already in progress, waiting for lock...")

    async with _rss_check_lock:
        try:
            _, _, user_updates_map = await fetch_formatted_rss_updates(
                subscriptions=subscriptions
            )
        except Exception as e:
            logger.error(f"Fetch updates failed: {e}")
            return 0

        if not user_updates_map:
            return 0

        sent_count = 0
        success_updates = []

        # 批量发送消息
        for (platform, uid), updates in user_updates_map.items():
            msg_header = f"📢 **RSS 订阅日报 ({len(updates)} 条更新)**\n\n"
            msg_body = ""
            current_batch = []

            for update in updates:
                item_text = (
                    f"🔹 **{update['feed_title']}**\n"
                    f"[{update['title']}]({update['link']})\n"
                    f"📝 {update['summary']}\n\n"
                )

                # 长度检查 & 分批发送
                if len(msg_header) + len(msg_body) + len(item_text) > 4000:
                    try:
                        await send_via_adapter(
                            chat_id=uid, text=msg_header + msg_body, platform=platform
                        )
                        success_updates.extend(current_batch)
                        sent_count += 1
                    except Exception as e:
                        logger.error(
                            f"Failed to send batch to {uid} on {platform}: {e}"
                        )

                    msg_body = ""
                    msg_header = "📢 **RSS 订阅日报 (续)**\n\n"
                    current_batch = []

                msg_body += item_text
                current_batch.append(update)

            if msg_body:
                try:
                    await send_via_adapter(
                        chat_id=uid, text=msg_header + msg_body, platform=platform
                    )
                    success_updates.extend(current_batch)
                    sent_count += 1
                except Exception as e:
                    logger.error(
                        f"Failed to send final batch to {uid} on {platform}: {e}"
                    )

        # 统一更新状态
        await mark_updates_as_read(success_updates)

        return sent_count


async def check_rss_updates_job():
    """检查 RSS 更新的任务 (定时调用)"""
    logger.info("Checking for RSS updates...")

    subscriptions = await get_all_subscriptions()
    if not subscriptions:
        logger.info("No subscriptions found.")
        return

    await check_and_send_rss_updates(subscriptions)


async def trigger_manual_rss_check(
    user_id: int,
    *,
    suppress_busy_message: bool = False,
) -> str:
    """
    [Tool Logic] 手动触发特定用户的 RSS 检查
    返回格式化后的更新内容文本，不直接发送。
    """
    # 获取锁
    if _rss_check_lock.locked():
        if suppress_busy_message:
            return ""
        return "⚠️ 正在进行定时更新检查，请稍后再试。"

    async with _rss_check_lock:
        formatted_text, all_pending, _ = await fetch_formatted_rss_updates(
            user_id=user_id
        )

        if all_pending:
            # 标记为已读 (因为即将返回给 Agent 展示)
            await mark_updates_as_read(all_pending)
            return formatted_text
        else:
            return ""


def start_rss_scheduler():
    """启动 RSS 检查定时任务"""
    interval = 30 * 60

    scheduler.add_job(
        check_rss_updates_job,
        "interval",
        seconds=interval,
        next_run_time=datetime.datetime.now() + datetime.timedelta(seconds=30),
        id="rss_check",
        replace_existing=True,
    )
    logger.info(f"RSS scheduler started, interval={interval}s")


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

                # 推送给用户 (via specific platform)
                await send_via_adapter(chat_id=user_id, text=message, platform=platform)
                logger.info(f"Sent stock quotes to user {user_id} on {platform}")

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
    try:
        user_id = int(str(user_id))
    except ValueError, TypeError:
        user_id = 0

    logger.info(
        f"[Cron] Executing scheduled skill: '{instruction}' for user {user_id} on {platform}"
    )

    try:
        from core.platform.models import UnifiedMessage, User, Chat, MessageType
        from core.agent_orchestrator import agent_orchestrator

        user_id_text = str(user_id)
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

        message_history = [
            {
                "role": "user",
                "parts": [
                    {
                        "text": (
                            f"[CRON TASK id={cron_task_id}]\n"
                            f"source=cron\n"
                            f"【系统级别最高指令】：你当前正在“执行”一个已被触发的系统定时任务！\n"
                            f"请从以下目标描述中提取需要真实执行的查询、分析等动作并**立刻执行它**。\n"
                            f"如果目标描述里带有“每天/每小时/定时”等字眼，请直接忽略这些时间修饰词，只执行里面提到的查天气、看新闻等实际动作！\n"
                            f"**绝对禁止**调用 scheduler_manager 去再次添加、创建新的定时任务（那会导致无限套娃循环）！\n\n"
                            f"目标任务描述：{instruction}"
                        )
                    }
                ],
            }
        ]

        # Execute via Agent Brain
        async for chunk in agent_orchestrator.handle_message(ctx, message_history):
            if chunk and chunk.strip():
                final_output.append(chunk)

        full_response = "".join(final_output).strip()
        # Push Notification Logic
        if need_push and user_id > 0:
            if full_response:
                logger.info(f"[Cron] Pushing result to {user_id} on {platform}")
                await send_via_adapter(
                    chat_id=user_id,
                    text=f"⏰ **定时任务执行报告 ({instruction})**\n\n{full_response}",
                    platform=platform,
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
        user_id = task.get("user_id", 0)
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
