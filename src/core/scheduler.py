"""
任务调度模块 - 处理定时提醒
"""

import logging
import datetime
import dateutil.parser
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
)
from core.state_store import get_all_active_tasks

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


# --- 动态 Skill 调度 ---


async def run_skill_cron_job(
    instruction: str,
    user_id: int | str = 0,
    platform: str = "telegram",
    need_push: bool = False,
    chat_id: str = "",
    session_id: str = "",
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
                metadata = (
                    {
                        "resource_binding": {
                            "platform": str(platform or "telegram"),
                            "chat_id": str(chat_id or "").strip(),
                        }
                    }
                    if str(chat_id or "").strip()
                    else None
                )
                (
                    target_platform,
                    target_chat_id,
                ) = await _resolve_proactive_delivery_target(
                    user_id_text,
                    platform,
                    metadata=metadata,
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
                        session_id=str(session_id or "").strip(),
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
        chat_id = str(task.get("chat_id") or "").strip()
        session_id = str(task.get("session_id") or "").strip()
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
                    args=[instruction, user_id, platform, need_push, chat_id, session_id],
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
