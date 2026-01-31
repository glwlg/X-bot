"""
任务调度模块 - 处理定时提醒
"""

import asyncio
import logging
import datetime
import dateutil.parser
import feedparser
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from core.skill_loader import skill_loader
from core.platform.registry import adapter_manager
from core.platform.models import UnifiedContext

from repositories import (
    add_reminder,
    delete_reminder,
    get_pending_reminders,
    get_all_subscriptions,
    update_subscription_status,
    get_user_watchlist,
    get_all_watchlist_users,
)
from repositories.task_repo import get_all_active_tasks
from repositories.chat_repo import save_message, get_latest_session_id

logger = logging.getLogger(__name__)

# Global Scheduler Instance
scheduler = AsyncIOScheduler()


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
            # Universal way?
            if platform == "telegram":
                # Telegram adapter has .bot
                await adapter.bot.send_message(
                    chat_id=chat_id, text=text, parse_mode=parse_mode, **kwargs
                )
            elif platform == "discord":
                # Discord adapter usually takes send_message with just chat_id/text
                # Check if discord adapter has send_message method matching signature
                # Assuming DiscordAdapter.send_message(self, chat_id, text)
                await adapter.send_message(chat_id=chat_id, text=text)
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
        await delete_reminder(reminder_id)


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

    # 存入数据库
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
    """从数据库加载未执行的提醒任务（Bot 启动时调用）"""
    logger.info("Loading pending reminders from database...")
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
    from core.config import gemini_client, GEMINI_MODEL

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
        response = await gemini_client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        return response.text.strip()
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
            from repositories import get_user_subscriptions

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
    for url, subs in feed_map.items():
        try:
            # Use run_in_executor to avoid blocking the event loop with synchronous feedparser
            feed = await loop.run_in_executor(None, feedparser.parse, url)
            if feed.bozo and feed.bozo_exception:
                continue
            if not feed.entries:
                continue

            latest_entry = feed.entries[0]
            entry_id = getattr(latest_entry, "id", getattr(latest_entry, "link", None))
            if not entry_id:
                continue

            for sub in subs:
                last_hash = sub["last_entry_hash"]
                if entry_id != last_hash:
                    # Found new content
                    title = latest_entry.get("title", "无标题")
                    link = latest_entry.get("link", url)
                    feed_title = feed.feed.get("title", "RSS 订阅")

                    # Content summary logic...
                    content = ""
                    if hasattr(latest_entry, "summary"):
                        content = latest_entry.summary
                    elif hasattr(latest_entry, "content") and latest_entry.content:
                        content = latest_entry.content[0].get("value", "")
                    elif hasattr(latest_entry, "description"):
                        content = latest_entry.description

                    import re

                    content_clean = re.sub(r"<[^>]+>", "", content).strip()

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
                        "sub_id": sub["id"],
                        "entry_id": entry_id,
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
    """更新数据库状态"""
    for update in pending_updates:
        try:
            await update_subscription_status(
                update["sub_id"], update["entry_id"], update["etag"], update["modified"]
            )
        except Exception as e:
            logger.error(
                f"Failed to update subscription status for sub {update['sub_id']}: {e}"
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

        # 统一更新数据库
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


async def trigger_manual_rss_check(user_id: int) -> str:
    """
    [Tool Logic] 手动触发特定用户的 RSS 检查
    返回格式化后的更新内容文本，不直接发送。
    """
    # 获取锁
    if _rss_check_lock.locked():
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
    # 测试期间改为 1 分钟
    interval = 60

    scheduler.add_job(
        check_rss_updates_job,
        "interval",
        seconds=interval,
        next_run_time=datetime.datetime.now() + datetime.timedelta(seconds=10),
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
        next_run_time=datetime.datetime.now() + datetime.timedelta(seconds=30),
        id="stock_push",
        replace_existing=True,
    )
    logger.info(f"Stock scheduler started, interval={interval}s")


# --- 动态 Skill 调度 ---


async def run_skill_cron_job(skill_name: str, instruction: str):
    """
    通用 Skill 定时任务执行器
    """
    if not skill_name:
        return

    logger.info(f"[Cron] Executing scheduled skill: {skill_name}")

    try:
        from services.skill_executor import skill_executor

        # 构造系统上下文
        system_ctx = UnifiedContext(
            platform="system",
            message=None,  # System messages have no triggers
        )

        if not instruction:
            instruction = "Execute scheduled maintenance/run_cron task."

        async for chunk, files in skill_executor.execute_skill(
            skill_name, instruction, ctx=system_ctx
        ):
            if chunk and chunk.strip():
                logger.info(f"[Cron {skill_name}] Output: {chunk[:100]}...")

    except Exception as e:
        logger.error(f"[Cron] Failed to run skill {skill_name}: {e}")


def start_dynamic_skill_scheduler():
    """
    扫描数据库中的任务并注册定时任务
    """
    logger.info("Scanning for dynamic skill jobs...")

    # 1. Load from DB (Primary Source)
    async def load_db_tasks():
        tasks = await get_all_active_tasks()
        count = 0
        for task in tasks:
            task_id = task["id"]
            skill_name = task["skill_name"]
            crontab = task["crontab"]
            instruction = task["instruction"]

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
                        id=f"cron_db_{task_id}_{skill_name}",
                        args=[skill_name, instruction],
                        replace_existing=True,
                    )
                    count += 1
                else:
                    logger.warning(
                        f"Invalid crontab format for task {task_id}: {crontab}"
                    )
            except Exception as e:
                logger.error(f"Failed to register DB cron for {skill_name}: {e}")

        logger.info(f"Registered {count} jobs from Database.")

    # Run task loader once
    # We can just run it now since we are in async context when starting services?
    # Or schedule it to run in 1s.

    # But scheduler needs to be running.
    # We will call start() in main.

    scheduler.add_job(
        load_db_tasks,
        "date",
        run_date=datetime.datetime.now() + datetime.timedelta(seconds=1),
    )
