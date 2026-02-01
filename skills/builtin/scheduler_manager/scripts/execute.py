from core.platform.models import UnifiedContext
from repositories.task_repo import (
    add_scheduled_task,
    get_all_active_tasks,
    delete_task,
)


async def execute(ctx: UnifiedContext, params: dict) -> str:
    """
    Execute scheduler management operations.
    """
    action = params.get("action", "list")
    user_id = int(ctx.message.user.id) if ctx.message and ctx.message.user else 0
    platform = (
        ctx.message.platform if ctx.message and ctx.message.platform else "telegram"
    )

    if action == "add":
        skill_name = params.get("skill_name")
        crontab = params.get("crontab")
        instruction = params.get("instruction")
        # Default True if not specified as 'false' string
        push_param = str(params.get("push", "true")).lower()
        need_push = push_param == "true" or push_param == "1"

        if not skill_name or not crontab:
            return "❌ 请提供 `skill_name` 和 `crontab` (e.g. '0 8 * * *')"

        if not instruction:
            instruction = f"Execute {skill_name}"

        try:
            task_id = await add_scheduled_task(
                skill_name, crontab, instruction, user_id, platform, need_push
            )

            # 立即触发 Scheduler 重载
            from core.scheduler import reload_scheduler_jobs

            await reload_scheduler_jobs()

            return (
                f"✅ 定时任务已添加 (ID: {task_id})\n"
                f"Skill: `{skill_name}`\n"
                f"Cron: `{crontab}`\n"
                f"Instruction: `{instruction}`\n"
                f"Push: `{'Yes' if need_push else 'No'}`\n"
                f"状态: 已立即生效"
            )
        except Exception as e:
            return f"❌ 添加失败: {e}"

    elif action == "list":
        tasks = await get_all_active_tasks()
        if not tasks:
            return "📭 当前没有活跃的定时任务。"

        msg = "📋 **定时任务列表**\n\n"
        for t in tasks:
            # Filter user? Or show all?
            # Usually admin wants to see all, user sees own.
            # strict permission check is better, but for now simple filter if user_id matches
            # or show all if we want transparency. Let's show all for now but mark ownership.

            owner_mark = "👤" if t.get("user_id") == user_id else "🤖"
            msg += f"{owner_mark} **ID: {t['id']}** | {t['skill_name']}\n"
            msg += f"   Cron: `{t['crontab']}`\n"
            msg += f"   Desc: `{t['instruction']}`\n"
            msg += f"   Push: {t.get('need_push', True)}\n\n"

        return msg

    elif action == "delete":
        task_id = params.get("task_id")
        if not task_id:
            return "❌ 请提供 `task_id`"

        try:
            # 权限检查：只能删除自己的任务？暂时不做强限制，信任 SkillAgent
            await delete_task(int(task_id))
            from core.scheduler import reload_scheduler_jobs

            await reload_scheduler_jobs()
            return f"✅ 任务 {task_id} 已删除并立即生效。"
        except Exception as e:
            return f"❌ 删除失败: {e}"

    return f"❌ 未知操作: {action}"
