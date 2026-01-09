"""
任务调度模块 - 处理定时提醒
"""
import logging
import datetime
import dateutil.parser
import feedparser
from telegram.ext import ContextTypes, JobQueue

from database import (
    add_reminder, 
    delete_reminder, 
    get_pending_reminders,
    get_all_subscriptions, 
    update_subscription_status
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
            # 只要有一个订阅了这个 URL 需要检查，就请求一次
            # 使用第一个订阅的 etag/modified 作为参考 (通常同一 URL 对不同用户是一样的)
            # 不过为了准确，还是只传 None，全面拉取，然后通过 id/link 比对
            # 为了节省流量，可以使用 etag。这里简单起见，不使用 conditional get (容易出错)
            # feedparser 会自动处理 etag 如果传入
            
            # 使用第一个 sub 的缓存头
            # first_sub = subs[0]
            # feed = feedparser.parse(url, etag=first_sub["last_etag"], modified=first_sub["last_modified"])
            
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
                    # 发送通知
                    title = latest_entry.get("title", "无标题")
                    link = latest_entry.get("link", url)
                    feed_title = feed.feed.get("title", "RSS 订阅")
                    
                    msg = f"📢 **{feed_title}** 更新了！\n\n[{title}]({link})"
                    
                    try:
                        await context.bot.send_message(
                            chat_id=sub["user_id"],
                            text=msg,
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.error(f"Failed to send RSS update to {sub['user_id']}: {e}")
                    
                    # 更新数据库状态
                    # 注意：如果多个用户订阅同一个，这里会多次更新同一个 sub id
                    # 但逻辑正确。
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
