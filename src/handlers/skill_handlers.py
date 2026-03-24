"""
Skill 管理 handlers - /teach, /skills 等命令
"""

import logging
import os
import re

from core.platform.models import UnifiedContext
from core.skill_menu import button_rows, cache_items, get_cached_item, make_callback, parse_callback

from core.config import is_user_admin
from extension.skills.registry import skill_registry as skill_loader
from handlers.base_handlers import (
    check_permission_unified,
    CONVERSATION_END,
    edit_callback_message,
)

logger = logging.getLogger(__name__)
SKILL_MENU_NS = "skills"

# 会话状态
WAITING_FOR_SKILL_DESC = 101


async def teach_command(ctx: UnifiedContext) -> int:
    """
    /teach 命令 - 教 Bot 新能力
    """
    if not await check_permission_unified(ctx):
        return CONVERSATION_END

    if not ctx.platform_ctx:
        return CONVERSATION_END

    args = ctx.platform_ctx.args
    if args:
        # 直接处理
        requirement = " ".join(args)
        return await process_teach(ctx, requirement)

    await ctx.reply(
        "💡 **教我新能力**\n\n"
        "请描述您想让我学会的新功能，例如：\n"
        "• 帮我在豆瓣上签到\n"
        "• 查询天气\n"
        "• 翻译日语\n\n"
        "发送 /cancel 取消。"
    )
    return WAITING_FOR_SKILL_DESC


async def handle_teach_input(ctx: UnifiedContext) -> int:
    """处理教学输入"""
    text = ctx.message.text
    if not text:
        await ctx.reply("请发送有效描述。")
        return WAITING_FOR_SKILL_DESC

    return await process_teach(ctx, text)


async def process_teach(ctx: UnifiedContext, requirement: str) -> int:
    """处理新能力学习"""
    msg = await ctx.reply(
        "💡 **提示**\n\n"
        "技能系统已升级。请直接通过聊天页面描述您的需求（例如：“请用这个规则创建一个新的技能……”），AI Agent 会自动为您编写对应 SOP 并保存。\n\n"
        "当前的 `/teach` 快捷指令暂不支持旧版直接触发扩展的功能。"
    )
    return CONVERSATION_END


async def handle_skill_callback(ctx: UnifiedContext) -> None:
    """处理 Skill 相关的回调"""
    data = ctx.callback_data
    if not data:
        return

    if data.startswith("skill_view_"):
        skill_name = data.replace("skill_view_", "")
        payload, ui = _build_skill_detail_payload(skill_name)
        await edit_callback_message(ctx, payload, ui=ui)
        return

    action, parts = parse_callback(data, SKILL_MENU_NS)
    if not action:
        return

    if action == "home":
        payload, ui = _build_skills_home_payload()
    elif action in {"builtin", "learned"}:
        page = int(str(parts[0] if parts else "0") or "0")
        payload, ui = _build_skills_list_payload(ctx, source=action, page=page)
    elif action == "detail":
        source = str(parts[0] if parts else "").strip()
        index = parts[1] if len(parts) >= 2 else ""
        cached = get_cached_item(ctx, SKILL_MENU_NS, source, index)
        if not cached:
            payload, ui = _build_skills_home_payload(
                prefix="❌ 技能列表已过期，请重新选择。"
            )
        else:
            payload, ui = _build_skill_detail_payload(str(cached))
    elif action == "doc":
        skill_name = str(parts[0] if parts else "").strip()
        sent = await _send_skill_documents(ctx, skill_name)
        payload, ui = _build_skill_detail_payload(
            skill_name,
            prefix=sent,
        )
    else:
        payload, ui = _build_skills_home_payload(prefix="❌ 未识别的技能菜单操作。")

    await edit_callback_message(ctx, payload, ui=ui)


async def skills_command(ctx: UnifiedContext) -> None:
    """
    /skills 命令 - 列出所有可用 Skills
    """

    if not await check_permission_unified(ctx):
        return

    payload, ui = _build_skills_home_payload()
    await ctx.reply(payload, ui=ui)


async def reload_skills_command(ctx: UnifiedContext) -> None:
    """
    /reload_skills 命令 - 重新加载所有 Skills（管理员）
    """
    if not is_user_admin(ctx.message.user.id):
        await ctx.reply("❌ 只有管理员可以执行此操作")
        return

    skill_loader.scan_skills()
    skill_loader.reload_skills()

    count = len(skill_loader.get_skill_index())
    await ctx.reply(f"🔄 已重新加载 {count} 个 Skills")


def _visible_skill_items() -> list[tuple[str, dict]]:
    output: list[tuple[str, dict]] = []
    for name, info in sorted(skill_loader.get_skill_index().items(), key=lambda item: item[0]):
        if bool(info.get("manager_only")):
            continue
        output.append((name, dict(info)))
    return output


def _build_skills_home_payload(*, prefix: str = "") -> tuple[str, dict]:
    items = _visible_skill_items()
    builtin = [name for name, info in items if str(info.get("source") or "") == "builtin"]
    learned = [name for name, info in items if str(info.get("source") or "") != "builtin"]

    lines: list[str] = []
    if prefix:
        lines.extend([prefix.strip(), ""])
    lines.extend(
        [
            "🧩 Skills",
            "",
            f"- 内置技能：`{len(builtin)}`",
            f"- 已学习技能：`{len(learned)}`",
            "",
            "点击下方按钮浏览详情。",
        ]
    )
    return "\n".join(lines), {
        "actions": [
            [
                {"text": "内置技能", "callback_data": make_callback(SKILL_MENU_NS, "builtin", 0)},
                {"text": "已学习技能", "callback_data": make_callback(SKILL_MENU_NS, "learned", 0)},
            ]
        ]
    }


def _build_skills_list_payload(
    ctx: UnifiedContext,
    *,
    source: str,
    page: int = 0,
) -> tuple[str, dict]:
    normalized = "builtin" if source == "builtin" else "learned"
    items = [
        (name, info)
        for name, info in _visible_skill_items()
        if ("builtin" if str(info.get("source") or "") == "builtin" else "learned")
        == normalized
    ]
    names = [name for name, _info in items]
    cache_items(ctx, SKILL_MENU_NS, normalized, names)

    if not items:
        return _build_skills_home_payload(prefix="📭 当前分组下没有可用技能。")

    page_size = 8
    total_pages = max(1, (len(items) + page_size - 1) // page_size)
    current_page = max(0, min(int(page or 0), total_pages - 1))
    start = current_page * page_size
    current_items = items[start : start + page_size]

    lines = [f"🧩 {'内置' if normalized == 'builtin' else '已学习'}技能（第 {current_page + 1}/{total_pages} 页）", ""]
    for offset, (name, info) in enumerate(current_items, start=start):
        lines.append(f"{offset + 1}. **{name}**")
        lines.append(f"   {_shorten(str(info.get('description') or ''), 72)}")

    buttons = [
        {
            "text": name[:20],
            "callback_data": make_callback(SKILL_MENU_NS, "detail", normalized, index),
        }
        for index, (name, _info) in enumerate(current_items, start=start)
    ]
    actions = button_rows(buttons, columns=2)
    nav_row = []
    if current_page > 0:
        nav_row.append(
            {"text": "⬅️ 上一页", "callback_data": make_callback(SKILL_MENU_NS, normalized, current_page - 1)}
        )
    if current_page < total_pages - 1:
        nav_row.append(
            {"text": "➡️ 下一页", "callback_data": make_callback(SKILL_MENU_NS, normalized, current_page + 1)}
        )
    if nav_row:
        actions.append(nav_row)
    actions.append([{"text": "返回总览", "callback_data": make_callback(SKILL_MENU_NS, "home")}])
    return "\n".join(lines), {"actions": actions}


def _shorten(text: str, limit: int = 60) -> str:
    raw = str(text or "").strip().replace("\n", " ")
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 1)] + "…"


def _build_skill_detail_payload(
    skill_name: str,
    *,
    prefix: str = "",
) -> tuple[str, dict]:
    info = skill_loader.get_skill_index().get(skill_name)
    if not info:
        return _build_skills_home_payload(prefix="❌ 技能不存在。")

    triggers = ", ".join(list(info.get("triggers") or [])[:8]) or "无"
    scripts = ", ".join(list(info.get("scripts") or [])[:6]) or "无"
    lines: list[str] = []
    if prefix:
        lines.extend([prefix.strip(), ""])
    lines.extend(
        [
            f"🧩 **{skill_name}**",
            "",
            f"- 来源：`{info.get('source')}`",
            f"- 描述：{str(info.get('description') or '').strip() or '无'}",
            f"- 触发词：{triggers}",
            f"- handlers：`{bool(info.get('platform_handlers'))}`",
            f"- 脚本：{scripts}",
        ]
    )
    actions = [
        [
            {"text": "发送 SKILL.md", "callback_data": make_callback(SKILL_MENU_NS, "doc", skill_name)},
        ],
        [
            {"text": "返回总览", "callback_data": make_callback(SKILL_MENU_NS, "home")},
        ],
    ]
    source = "builtin" if str(info.get("source") or "") == "builtin" else "learned"
    actions[1].insert(
        0,
        {"text": "返回列表", "callback_data": make_callback(SKILL_MENU_NS, source, 0)},
    )
    return "\n".join(lines), {"actions": actions}


async def _send_skill_documents(ctx: UnifiedContext, skill_name: str) -> str:
    info = skill_loader.get_skill_index().get(skill_name)
    if not info:
        return "❌ 技能不存在。"

    skill_md_path = str(info.get("skill_md_path") or "").strip()
    if not skill_md_path or not os.path.exists(skill_md_path):
        return "❌ SKILL.md 文件不存在。"

    if str(ctx.message.platform or "").strip().lower() == "dingtalk":
        return f"⚠️ 当前平台不支持直接发送本地文档。\n路径：`{skill_md_path}`"

    try:
        await ctx.reply_document(
            skill_md_path,
            filename="SKILL.md",
            caption=f"📄 {skill_name} - SKILL.md",
        )
        return "📄 技能文档已发送。"
    except Exception as exc:
        logger.error("Failed to send skill document: %s", exc)
        return f"❌ 发送文档失败：{exc}"
