"""
任务调度模块 - 处理定时提醒
"""
import logging
import datetime
import dateutil.parser
import feedparser
from telegram.ext import ContextTypes, JobQueue

from repositories import (
    add_reminder, 
    delete_reminder, 
    get_pending_reminders,
    get_all_subscriptions, 
    update_subscription_status,
    get_user_watchlist,
    get_all_watchlist_users,
)

logger = logging.getLogger(__name__)


async def send_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    """发送提醒的工作任务"""
    job = context.job
    # job.data 存储了 reminder_id, user_id, chat_id, message
    reminder_id = job.data["id"]
    chat_id = job.data["chat_id"]
    message = job.data["message"]
    
    logger.info(f"Triggering reminder {reminder_id} for chat {chat_id}")
    
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⏰ **提醒**\n\n{message}",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Failed to send reminder {reminder_id}: {e}")
    finally:
         # 无论发送成功与否（可能是用户封锁了Bot），都删除任务，避免重复执行
         # 改进点：如果是网络错误，可以重试，但这里简化处理
         await delete_reminder(reminder_id)


async def schedule_reminder(
    job_queue: JobQueue,
    user_id: int,
    chat_id: int,
    message: str,
    trigger_time: datetime.datetime
) -> bool:
    """安排一个新的提醒任务"""
    now = datetime.datetime.now().astimezone()
    delay = (trigger_time - now).total_seconds()
    
    if delay < 0:
        logger.warning("Trigger time is in the past, running immediately")
        delay = 0

    # 存入数据库
    # 注意：sqlite 存 timestamp 需要转字符串 (isoformat)
    # 并且保持时区信息很重要
    reminder_id = await add_reminder(user_id, chat_id, message, trigger_time.isoformat())
    
    # 加入 JobQueue
    job_queue.run_once(
        send_reminder_job,
        when=delay,
        data={
            "id": reminder_id,
            "user_id": user_id,
            "chat_id": chat_id,
            "message": message
        }
    )
    return True


async def load_jobs_from_db(job_queue: JobQueue):
    """从数据库加载未执行的提醒任务（Bot 启动时调用）"""
    logger.info("Loading pending reminders from database...")
    reminders = await get_pending_reminders()
    
    count = 0
    now = datetime.datetime.now().astimezone()
    
    for row in reminders:
        reminder_id = row["id"]
        trigger_time_str = row["trigger_time"]
        
        try:
            # 解析时间
            trigger_time = dateutil.parser.isoparse(trigger_time_str)
            
            # 确保此时区意识到 (aware)，如果 db 里存的是 naive，默认视作 local
            if trigger_time.tzinfo is None:
                 trigger_time = trigger_time.replace(tzinfo=now.tzinfo)

            delay = (trigger_time - now).total_seconds()
            
            # 如果错过了时间，稍微延迟一点立即执行 (e.g. 5s)
            if delay < 0:
                delay = 5 
                
            job_queue.run_once(
                send_reminder_job,
                when=delay,
                data={
                    "id": reminder_id,
                    "user_id": row["user_id"],
                    "chat_id": row["chat_id"],
                    "message": row["message"]
                }
            )
            count += 1
            
            logger.info(f"Loaded {count} pending reminders.")
            
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
        "请为以下新闻/文章生成一段简洁的中文摘要（100-150字），"
        "突出核心信息和要点。直接输出摘要内容，不要加任何前缀。\n\n"
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


async def check_rss_updates_job(context: ContextTypes.DEFAULT_TYPE):
    """检查 RSS 更新的任务"""
    logger.info("Checking for RSS updates...")
    
    subscriptions = await get_all_subscriptions()
    if not subscriptions:
        logger.info("No subscriptions found.")
        return

    # 按 feed_url 分组，避免重复请求同一个 URL
    # {url: [sub1, sub2, ...]}
    feed_map = {}
    for sub in subscriptions:
        url = sub["feed_url"]
        if url not in feed_map:
            feed_map[url] = []
        feed_map[url].append(sub)
        
    for url, subs in feed_map.items():
        try:
            # 简单实现：全量拉取，只检查 ID/Link
            feed = feedparser.parse(url)
            
            if feed.bozo and feed.bozo_exception:
                logger.warning(f"Error parsing feed {url}: {feed.bozo_exception}")
                continue
                
            if not feed.entries:
                continue
                
            latest_entry = feed.entries[0]
            # 生成 hash (用 link 或 id)
            entry_id = getattr(latest_entry, "id", getattr(latest_entry, "link", None))
            
            if not entry_id:
                continue
            
            # 检查每个用户的订阅状态
            for sub in subs:
                last_hash = sub["last_entry_hash"]
                
                # 如果是新的
                if entry_id != last_hash:
                    # 提取内容用于生成摘要
                    title = latest_entry.get("title", "无标题")
                    link = latest_entry.get("link", url)
                    feed_title = feed.feed.get("title", "RSS 订阅")
                    
                    # 提取文章内容
                    content = ""
                    if hasattr(latest_entry, "summary"):
                        content = latest_entry.summary
                    elif hasattr(latest_entry, "content") and latest_entry.content:
                        content = latest_entry.content[0].get("value", "")
                    elif hasattr(latest_entry, "description"):
                        content = latest_entry.description
                    
                    # 清理 HTML 标签
                    import re
                    content = re.sub(r'<[^>]+>', '', content)
                    content = content.strip()
                    
                    # 生成 AI 摘要
                    if content:
                        summary = await generate_entry_summary(title, content, link)
                    else:
                        summary = "暂无摘要"
                    
                    msg = (
                        f"📢 **{feed_title}** 更新了！\n\n"
                        f"**{title}**\n\n"
                        f"📝 {summary}\n\n"
                        f"🔗 [阅读全文]({link})"
                    )
                    
                    try:
                        await context.bot.send_message(
                            chat_id=sub["user_id"],
                            text=msg,
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.error(f"Failed to send RSS update to {sub['user_id']}: {e}")
                    
                    # 更新数据库状态
                    await update_subscription_status(
                        sub["id"], 
                        entry_id, 
                        getattr(feed, "etag", None), 
                        getattr(feed, "modified", None)
                    )
                    
        except Exception as e:
            logger.error(f"Error checking feed {url}: {e}")


def start_rss_scheduler(job_queue: JobQueue):
    """启动 RSS 检查定时任务"""
    # 每 30 分钟检查一次
    # interval = 30 * 60
    # 测试期间改为 5 分钟
    interval = 30 * 60 
    
    job_queue.run_repeating(
        check_rss_updates_job,
        interval=interval,
        first=10, # 启动 10 秒后第一次运行
        name="rss_check"
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
    
    return (morning_start <= current_time <= morning_end or 
            afternoon_start <= current_time <= afternoon_end)


async def stock_push_job(context: ContextTypes.DEFAULT_TYPE):
    """每 10 分钟推送股票行情"""
    if not is_trading_time():
        logger.debug("Not trading time, skipping stock push")
        return
    
    logger.info("Starting stock push job...")
    
    # 延迟导入避免循环引用
    from services.stock_service import fetch_stock_quotes, format_stock_message
    
    try:
        # 获取所有有自选股的用户
        user_ids = await get_all_watchlist_users()
        
        if not user_ids:
            logger.info("No users with watchlist, skipping")
            return
        
        for user_id in user_ids:
            try:
                # 获取用户自选股
                watchlist = await get_user_watchlist(user_id)
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
                
                # 推送给用户
                await context.bot.send_message(
                    chat_id=user_id,
                    text=message,
                    parse_mode="Markdown"
                )
                logger.info(f"Sent stock quotes to user {user_id}")
                
            except Exception as e:
                logger.error(f"Failed to send stock quotes to {user_id}: {e}")
                
    except Exception as e:
        logger.error(f"Stock push job error: {e}")


def start_stock_scheduler(job_queue: JobQueue):
    """启动股票推送定时任务"""
    interval = 10 * 60  # 10 分钟
    
    job_queue.run_repeating(
        stock_push_job,
        interval=interval,
        first=30,  # 启动 30 秒后第一次运行
        name="stock_push"
    )
    logger.info(f"Stock scheduler started, interval={interval}s")
