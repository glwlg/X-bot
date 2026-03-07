"""
RSS Subscription and Monitoring Skill Script
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import urllib.parse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SRC_ROOT = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import feedparser
import httpx
from core.platform.models import UnifiedContext
from core.skill_cli import (
    add_common_arguments,
    merge_params,
    prepare_default_env,
    run_execute_cli,
)

prepare_default_env(REPO_ROOT)

from core.state_store import (
    add_subscription,
    delete_subscription,
    delete_subscription_by_id,
    get_user_subscriptions,
)
from stats import increment_stat

logger = logging.getLogger(__name__)


def _parse_rss_subcommand(text: str) -> tuple[str, str]:
    raw = str(text or "").strip()
    if not raw:
        return "list", ""

    parts = raw.split(maxsplit=2)
    if not parts:
        return "list", ""
    if not parts[0].startswith("/rss"):
        return "help", ""
    if len(parts) == 1:
        return "list", ""

    sub = str(parts[1] or "").strip().lower()
    args = str(parts[2] if len(parts) >= 3 else "").strip()

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
        "`/rss list`\n"
        "`/rss add <RSS URL>`\n"
        "`/rss monitor <关键词>`\n"
        "`/rss remove <RSS URL>`\n"
        "`/rss refresh`\n"
        "`/rss help`"
    )


async def execute(ctx: UnifiedContext, params: dict, runtime=None) -> str:
    """执行 RSS 订阅或关键词监控"""
    action = str(params.get("action") or "").strip().lower()
    action = action.replace("-", "_").replace(" ", "_")

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
    if action:
        if any(token in action for token in ("refresh", "check", "update", "latest")):
            action = "refresh"
        elif any(token in action for token in ("remove", "delete", "unsub")):
            action = "remove"
        elif any(token in action for token in ("list", "subs", "subscription", "feed")):
            action = "list"
    # 支持 url 或 keyword 参数
    raw_target = str(params.get("url") or params.get("keyword", "") or "").strip()
    url = raw_target
    command_token = raw_target
    if command_token.startswith("/"):
        command_token = command_token[1:]
    command_token = command_token.strip().lower()
    message_text = str(getattr(getattr(ctx, "message", None), "text", "") or "").lower()

    if not action and command_token in {
        "list",
        "list_subs",
        "subscriptions",
        "subs",
    }:
        action = "list"
        url = ""
    elif not action and command_token in {"refresh", "check", "run", "latest"}:
        action = "refresh"
        url = ""
    elif not action and command_token in {
        "remove",
        "unsubscribe",
        "delete",
    }:
        action = "remove"
        url = ""

    if not action:
        action = "refresh" if not url else "add"

    if action in {"check", "run", "latest"}:
        action = "refresh"

    if action == "list":
        update_tokens = (
            "更新",
            "最新",
            "check",
            "refresh",
            "update",
            "latest",
            "有没有",
        )
        explicit_list_only_tokens = (
            "只看列表",
            "仅列表",
            "list only",
            "只列出",
        )
        has_update_goal = any(token in message_text for token in update_tokens)
        list_only_goal = any(
            token in message_text for token in explicit_list_only_tokens
        )
        if has_update_goal and not list_only_goal:
            action = "refresh"

    if action == "list":
        url = ""

    if action == "refresh":
        msg = await refresh_user_subscriptions(ctx)
        # if msg: await ctx.reply(msg)
        return {"text": msg, "ui": {}}

    if action == "list":
        return await list_subs_command(ctx)

    if action == "remove":
        if url:
            # Direct remove if URL is provided
            user_id = ctx.message.user.id
            success = await delete_subscription(user_id, url)
            if success:
                # await ctx.reply(f"🗑️ 已取消订阅：`{url}`")
                return {"text": f"✅ 已取消订阅: {url}", "ui": {}}
            else:
                # await ctx.reply(...)
                return {"text": f"❌ 取消失败，未找到该订阅：`{url}`", "ui": {}}
            # Interactive remove
            return await show_unsubscribe_menu(ctx)

    # Default: Add
    return await process_subscribe(ctx, url)


def register_handlers(adapter_manager):
    """注册 RSS 二级命令和 Callback"""
    from core.config import is_user_allowed

    async def cmd_rss(ctx):
        if not await is_user_allowed(ctx.message.user.id):
            return

        sub, args = _parse_rss_subcommand(ctx.message.text or "")
        if sub == "list":
            return await list_subs_command(ctx)

        if sub == "add":
            target = args.strip()
            if not target:
                return {"text": "用法: `/rss add <RSS URL>`", "ui": {}}
            return await process_subscribe(ctx, target)

        if sub == "monitor":
            keyword = args.strip()
            if not keyword:
                return {"text": "用法: `/rss monitor <关键词>`", "ui": {}}
            return await process_monitor(ctx, keyword)

        if sub == "remove":
            target = args.strip()
            if target:
                success = await delete_subscription(ctx.message.user.id, target)
                if success:
                    return {"text": f"✅ 已取消订阅: {target}", "ui": {}}
                return {"text": f"❌ 未找到订阅: {target}", "ui": {}}
            return await show_unsubscribe_menu(ctx)

        if sub == "refresh":
            msg = await refresh_user_subscriptions(ctx)
            return {"text": msg, "ui": {}}

        return {"text": _rss_usage_text(), "ui": {}}

    adapter_manager.on_command("rss", cmd_rss, description="RSS/新闻订阅管理")

    # Callbacks
    adapter_manager.on_callback_query("^unsub_", handle_unsubscribe_callback)


async def fetch_feed_safe(url: str):
    """Safely fetch and parse RSS feed asynchronously"""
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        content = response.content

        loop = asyncio.get_running_loop()
        # Parse content in thread pool
        return await loop.run_in_executor(None, feedparser.parse, content)


async def process_subscribe(ctx: UnifiedContext, url: str):
    """实际处理订阅逻辑 (Returns dict)"""
    try:
        user_id = ctx.message.user.id
    except (ValueError, TypeError):
        user_id = ctx.message.user.id
        logger.warning(f"Failed to cast user_id {user_id} to int")

    if not url.startswith("http"):
        # 尝试作为关键词处理 (集成 Monitor 功能)
        logger.info(f"Input '{url}' is not a URL, treating as keyword for monitor.")

        # 使用 Google News 搜索该关键词
        keywords = re.split(r"[、,，]+", url.strip())
        keywords = [k.strip() for k in keywords if k.strip()]

        if not keywords:
            return {"text": "❌ 请输入有效的 HTTP/HTTPS 链接或监控关键词。", "ui": {}}

        # 如果是多个关键词，直接调用 process_monitor 批量处理
        return await process_monitor(ctx, url)

    try:
        # msg = await ctx.reply("🔍 正在验证 RSS 源...")

        # Use safe async fetch
        try:
            feed = await fetch_feed_safe(url)
        except Exception as e:
            logger.error(f"Feed fetch failed: {e}")
            # await ctx.edit_message(...)
            return {"text": f"❌ 无法连接到 RSS 源: {e}", "ui": {}}

        title = feed.feed.get("title", url)
        if not title:
            title = url

        try:
            platform = ctx.message.platform if ctx.message.platform else "telegram"
            await add_subscription(user_id, url, title, platform=platform)
            # await ctx.edit_message(...)
            try:
                uid_int = int(user_id)
                await increment_stat(uid_int, "subscriptions_added")
            except:
                pass
            return {
                "text": f"✅ **订阅成功！**\n\n源：{title}\nBot 将每 30 分钟检查一次更新。",
                "ui": {},
            }
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                return {"text": "⚠️ 您已经订阅过这个源了。", "ui": {}}
            else:
                return {"text": f"❌ 订阅失败: {e}", "ui": {}}

    except Exception as e:
        logger.error(f"Subscribe error: {e}")
        return {"text": "❌ 无法访问该 RSS 源。", "ui": {}}


async def process_monitor(ctx: UnifiedContext, keyword: str):
    """实际处理监控逻辑，支持多关键词 (Returns dict)"""
    user_id = ctx.message.user.id

    keywords = re.split(r"[、,，]+", keyword.strip())
    keywords = [k.strip() for k in keywords if k.strip()]

    if not keywords:
        return {"text": "❌ 请输入有效的关键词。", "ui": {}}

    # msg = await ctx.reply(f"🔍 正在配置 {len(keywords)} 个关键词监控...")

    platform = ctx.message.platform if ctx.message.platform else "telegram"

    success_list = []
    failed_list = []
    existed_list = []

    for kw in keywords:
        encoded_keyword = urllib.parse.quote(kw)
        rss_url = f"https://news.google.com/rss/search?q={encoded_keyword}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
        title = f"监控: {kw}"

        try:
            await add_subscription(user_id, rss_url, title, platform=platform)
            success_list.append(kw)
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                existed_list.append(kw)
            else:
                logger.error(f"Monitor error for '{kw}': {e}")
                failed_list.append(kw)

    result_parts = []
    if success_list:
        result_parts.append(f"✅ 已添加监控 ({platform})：{', '.join(success_list)}")
    if existed_list:
        result_parts.append(f"⚠️ 已存在：{', '.join(existed_list)}")
    if failed_list:
        result_parts.append(f"❌ 添加失败：{', '.join(failed_list)}")

    result_msg = (
        "**监控设置完成！**\n\n"
        + "\n".join(result_parts)
        + "\n\n来源：Google News\nBot 将每 30 分钟推送相关新闻。"
    )

    # await ctx.edit_message(...)
    return {"text": result_msg, "ui": {}}


async def list_subs_command(ctx: UnifiedContext) -> str:
    """返回订阅列表（用于 /rss list）"""
    # Note: Permission check removed from here, should be done by caller/agent

    user_id = ctx.message.user.id

    subs = await get_user_subscriptions(user_id)

    if not subs:
        # await ctx.reply("📭 您当前没有订阅任何 RSS 源。")
        return {"text": "📭 您当前没有订阅任何 RSS 源。", "ui": {}}

    msg = "📋 **您的订阅列表**：\n\n"
    for sub in subs:
        title = sub["title"]
        url = sub["feed_url"]
        msg += f"• [{title}]({url})\n\n"

    msg += "也可以直接点击下方按钮取消订阅："

    actions = []
    temp_row = []
    for sub in subs:
        short_title = (
            sub["title"][:10] + ".." if len(sub["title"]) > 10 else sub["title"]
        )
        btn = {"text": f"❌ {short_title}", "callback_data": f"unsub_{sub['id']}"}
        temp_row.append(btn)

        if len(temp_row) == 2:
            actions.append(temp_row)
            temp_row = []

    if temp_row:
        actions.append(temp_row)

    logger.info(f"list_subs_command text:{msg} actions: {actions}")
    return {"text": msg, "ui": {"actions": actions}}


async def refresh_user_subscriptions(ctx: UnifiedContext) -> str:
    """
    [Tool] 手动刷新当前用户的订阅
    """
    user_id = ctx.message.user.id

    if ctx.platform_ctx:
        try:
            await ctx.platform_ctx.bot.send_chat_action(
                chat_id=ctx.message.chat.id, action="typing"
            )
        except:
            pass

    from core.scheduler import trigger_manual_rss_check

    result_text = await trigger_manual_rss_check(user_id)

    if result_text:
        return result_text
    else:
        return "✅ 检查完成，您订阅的内容暂时没有更新。"


async def show_unsubscribe_menu(ctx: UnifiedContext) -> None:
    """显示取消订阅菜单"""
    user_id = ctx.message.user.id
    subs = await get_user_subscriptions(user_id)

    if not subs:
        return {"text": "📭 您当前没有订阅任何内容。", "ui": {}}

    actions = []
    for sub in subs:
        title = sub["title"] or sub["feed_url"][:30]
        actions.append([{"text": f"❌ {title}", "callback_data": f"unsub_{sub['id']}"}])

    actions.append([{"text": "🚫 取消", "callback_data": "unsub_cancel"}])

    return {"text": "📋 **请选择要取消的订阅**：", "ui": {"actions": actions}}


async def handle_unsubscribe_callback(ctx: UnifiedContext) -> None:
    """处理取消订阅按钮回调"""
    data = ctx.callback_data
    if not data:
        return

    await ctx.answer_callback()

    if data == "unsub_cancel":
        # await ctx.reply("👌 已取消操作。")
        return "👌 已取消操作。"

    try:
        sub_id = int(data.replace("unsub_", ""))
        user_id = ctx.callback_user_id
    except ValueError:
        # await ctx.reply("❌ 无效的操作。")
        return "❌ 无效的操作。"

    success = await delete_subscription_by_id(sub_id, user_id)

    if success:
        return "✅ 订阅已取消。"
    else:
        return "❌ 取消失败，订阅可能已不存在。"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="RSS subscribe skill CLI bridge.",
    )
    add_common_arguments(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List subscriptions")

    add_parser = subparsers.add_parser("add", help="Add an RSS feed subscription")
    add_parser.add_argument("target", help="RSS URL or keyword")

    monitor_parser = subparsers.add_parser(
        "monitor",
        help="Monitor one or more keywords",
    )
    monitor_parser.add_argument("keyword", help="Keyword or comma-separated keywords")

    remove_parser = subparsers.add_parser("remove", help="Remove a subscription")
    remove_parser.add_argument("target", help="RSS URL or keyword")

    subparsers.add_parser("refresh", help="Refresh subscriptions now")
    return parser


def _params_from_args(args: argparse.Namespace) -> dict:
    command = str(args.command or "").strip().lower()
    if command == "list":
        return merge_params(args, {"action": "list"})
    if command == "add":
        target = str(args.target or "").strip()
        return merge_params(args, {"action": "add", "url": target})
    if command == "monitor":
        keyword = str(args.keyword or "").strip()
        return merge_params(args, {"action": "monitor", "keyword": keyword})
    if command == "remove":
        target = str(args.target or "").strip()
        return merge_params(args, {"action": "remove", "url": target})
    if command == "refresh":
        return merge_params(args, {"action": "refresh"})
    raise SystemExit(f"unsupported command: {command}")


async def _run() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return await run_execute_cli(execute, args=args, params=_params_from_args(args))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
