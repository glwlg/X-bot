from core.platform.models import UnifiedContext
from repositories.task_repo import (
    add_scheduled_task,
    get_all_active_tasks,
    delete_task,
)
import logging

logger = logging.getLogger(__name__)


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
        crontab = params.get("crontab")
        instruction = params.get("instruction")
        # Default True if not specified as 'false' string
        push_param = str(params.get("push", "true")).lower()
        need_push = push_param == "true" or push_param == "1"

        if not instruction:
            return "❌ 请提供 `instruction`"

        try:
            task_id = await add_scheduled_task(
                crontab, instruction, user_id, platform, need_push
            )

            # 立即触发 Scheduler 重载
            from core.scheduler import reload_scheduler_jobs

            await reload_scheduler_jobs()

            return (
                f"✅ 定时任务已添加 (ID: {task_id})\n"
                f"Cron: `{crontab}`\n"
                f"Instruction: `{instruction}`\n"
                f"Push: `{'Yes' if need_push else 'No'}`\n"
                f"状态: 已立即生效"
            )
        except Exception as e:
            return f"❌ 添加失败: {e}"

    elif action == "list":
        return await list_tasks_command(ctx)

    elif action == "delete":
        task_id = params.get("task_id")
        if not task_id:
            return "❌ 请提供 `task_id`"

        try:
            await delete_task(int(task_id))
            from core.scheduler import reload_scheduler_jobs

            await reload_scheduler_jobs()
            return f"✅ 任务 {task_id} 已删除并立即生效。"
        except Exception as e:
            return f"❌ 删除失败: {e}"

    return f"❌ 未知操作: {action}"


def register_handlers(adapter_manager):
    """注册 Scheduler 相关的 Command 和 Callback"""
    from core.config import is_user_allowed

    async def cmd_tasks(ctx):
        if not await is_user_allowed(ctx.message.user.id):
            return
        return await list_tasks_command(ctx)

    async def cmd_del_task(ctx):
        if not await is_user_allowed(ctx.message.user.id):
            return
        args = []
        if ctx.message.text:
            parts = ctx.message.text.split()
            if len(parts) > 1:
                args = parts[1:]

        if args:
            try:
                task_id = int(args[0])
                await delete_task(task_id)
                from core.scheduler import reload_scheduler_jobs

                await reload_scheduler_jobs()
                return f"✅ 任务 {task_id} 已删除。"
            except ValueError:
                return "❌ 任务 ID 必须是数字。"
            except Exception as e:
                return f"❌ 删除失败: {e}"
        else:
            return await show_delete_menu(ctx)

    adapter_manager.on_command("tasks", cmd_tasks, description="查看定时任务列表")
    adapter_manager.on_command("del_task", cmd_del_task, description="删除定时任务")

    # Callbacks
    adapter_manager.on_callback_query("^sch_del_", handle_task_delete_callback)


async def list_tasks_command(ctx: UnifiedContext):
    """处理 /tasks 命令，显示带按钮的列表"""
    user_id = int(ctx.message.user.id) if ctx.message.user else 0
    tasks = await get_all_active_tasks()

    if not tasks:
        # return {"text": "📭 当前没有活跃的定时任务。", "ui": {}}
        # Skill execute return expectation can be str or dict with text/ui
        # But here we are called by execute or cmd_tasks.
        # execute handles dict return nicely? execute implementation above returns directly.
        # Let's return dict format which is supported by unified_adapter for skills usually,
        # but check how cmd handles it.
        # The adapter generally handles str or dict.
        return {"text": "📭 当前没有活跃的定时任务。", "ui": {}}

    msg = "📋 **定时任务列表**\n\n"
    # Filter/Sort?
    # Separate own tasks vs others
    own_tasks = []
    other_tasks = []

    for t in tasks:
        if t.get("user_id") == user_id:
            own_tasks.append(t)
        else:
            other_tasks.append(t)

    # Display logic: show all but distinguish
    all_sorted = own_tasks + other_tasks

    for t in all_sorted:
        owner_mark = "👤" if t.get("user_id") == user_id else "🤖"
        msg += f"{owner_mark} **ID: {t['id']}**\n"
        msg += f"   Cron: `{t['crontab']}`\n"
        msg += f"   Desc: `{t['instruction']}`\n"
        msg += f"   Push: {t.get('need_push', True)}\n\n"

    # Actions: Create delete buttons for own tasks (or all if admin?)
    # Assuming user can delete any task for now as per previous logic "trust SkillAgent"
    # But for UI clutter, maybe just first few or allow all.
    # Let's create actions for ALL tasks for now.

    actions = []
    temp_row = []
    for t in all_sorted:
        # Label: "❌ {id} {instruction[:5]}"
        instr_short = (
            t["instruction"][:8] + ".."
            if len(t["instruction"]) > 8
            else t["instruction"]
        )
        btn_text = f"❌ {t['id']} {instr_short}"
        btn_data = f"sch_del_{t['id']}"

        temp_row.append({"text": btn_text, "callback_data": btn_data})
        if len(temp_row) == 2:
            actions.append(temp_row)
            temp_row = []

    if temp_row:
        actions.append(temp_row)

    return {"text": msg, "ui": {"actions": actions}}


async def show_delete_menu(ctx: UnifiedContext):
    """显示删除菜单"""
    return await list_tasks_command(ctx)


async def handle_task_delete_callback(ctx: UnifiedContext):
    """处理删除按钮回调"""
    data = ctx.callback_data
    if not data:
        return

    await ctx.answer_callback()

    try:
        task_id = int(data.replace("sch_del_", ""))
    except ValueError:
        return "❌ 无效的操作。"

    try:
        await delete_task(task_id)
        from core.scheduler import reload_scheduler_jobs

        await reload_scheduler_jobs()

        # Optionally update the list if we can edit the message
        # But simple return text is also fine for notification
        return f"✅ 任务 {task_id} 已删除。"
    except Exception as e:
        return f"❌ 删除失败: {e}"
