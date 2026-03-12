"""RSS Subscription Skill Script."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
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
    create_subscription,
    delete_subscription,
    get_subscription,
    list_subscriptions,
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
        "`/rss remove <订阅ID>`\n"
        "`/rss refresh`\n"
        "`/rss help`"
    )


def _normalize_action(value: str) -> str:
    action = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
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
    if action in {"check", "run", "latest"}:
        return "refresh"
    return action


def _extract_target(params: dict) -> str:
    raw_target = str(
        params.get("url")
        or params.get("keyword")
        or params.get("target")
        or params.get("query")
        or ""
    ).strip()
    command_token = raw_target
    if command_token.startswith("/"):
        command_token = command_token[1:]
    token = command_token.strip().lower()
    if token in {"list", "list_subs", "subscriptions", "subs"}:
        return ""
    return raw_target


async def execute(ctx: UnifiedContext, params: dict, runtime=None) -> dict:
    action = _normalize_action(str(params.get("action") or ""))
    raw_target = str(
        params.get("url")
        or params.get("keyword")
        or params.get("target")
        or params.get("query")
        or ""
    ).strip()
    target = _extract_target(params)
    message_text = str(getattr(getattr(ctx, "message", None), "text", "") or "").lower()

    if not action:
        raw_token = raw_target.lower().lstrip("/")
        if raw_token in {"list", "list_subs", "subscriptions", "subs"}:
            action = "list"
            target = ""
        elif raw_token in {"refresh", "check", "run", "latest"}:
            action = "refresh"
            target = ""
        elif raw_token in {"remove", "unsubscribe", "delete"}:
            action = "remove"
            target = ""
        else:
            token = str(target or "").strip().lower().lstrip("/")
            if token in {"list", "list_subs", "subscriptions", "subs"}:
                action = "list"
                target = ""
            elif token in {"refresh", "check", "run", "latest"}:
                action = "refresh"
                target = ""
            elif token in {"remove", "unsubscribe", "delete"}:
                action = "remove"
                target = ""
            else:
                action = "refresh" if not target else "add"

    if action == "list":
        update_tokens = ("更新", "最新", "check", "refresh", "update", "latest", "有没有")
        explicit_list_only_tokens = ("只看列表", "仅列表", "list only", "只列出")
        has_update_goal = any(token in message_text for token in update_tokens)
        list_only_goal = any(token in message_text for token in explicit_list_only_tokens)
        if has_update_goal and not list_only_goal:
            action = "refresh"

    if action == "refresh":
        return {"text": await refresh_user_subscriptions(ctx), "ui": {}}
    if action == "list":
        return await list_subs_command(ctx)
    if action == "remove":
        if target:
            return await remove_subscription_by_target(ctx, target)
        return await show_unsubscribe_menu(ctx)
    if action == "monitor":
        return {"text": "❌ 关键词监控已下线，仅支持真实 RSS/Atom 订阅。", "ui": {}}
    if action == "add":
        if not target:
            return {"text": "用法: `/rss add <RSS URL>`", "ui": {}}
        return await process_subscribe(ctx, target)
    return {"text": _rss_usage_text(), "ui": {}}


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
            if not args.strip():
                return {"text": "用法: `/rss add <RSS URL>`", "ui": {}}
            return await process_subscribe(ctx, args.strip())
        if sub == "monitor":
            return {"text": "❌ 关键词监控已下线，仅支持真实 RSS/Atom 订阅。", "ui": {}}
        if sub == "remove":
            if args.strip():
                return await remove_subscription_by_target(ctx, args.strip())
            return await show_unsubscribe_menu(ctx)
        if sub == "refresh":
            return {"text": await refresh_user_subscriptions(ctx), "ui": {}}
        return {"text": _rss_usage_text(), "ui": {}}

    adapter_manager.on_command("rss", cmd_rss, description="RSS 订阅管理")
    adapter_manager.on_callback_query("^unsub_", handle_unsubscribe_callback)


async def fetch_feed_safe(url: str):
    """Safely fetch and parse RSS feed asynchronously."""
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        content = response.content

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, feedparser.parse, content)


async def process_subscribe(ctx: UnifiedContext, url: str) -> dict:
    target = str(url or "").strip()
    if not target.startswith(("http://", "https://")):
        return {"text": "❌ `/rss add` 只接受真实 RSS/Atom 链接。", "ui": {}}

    try:
        feed = await fetch_feed_safe(target)
    except Exception as exc:
        logger.error("Feed fetch failed: %s", exc)
        return {"text": f"❌ 无法连接到 RSS 源: {exc}", "ui": {}}

    title = str(feed.feed.get("title") or "").strip() or target
    platform = str(ctx.message.platform or "telegram").strip() or "telegram"

    try:
        created = await create_subscription(
            ctx.message.user.id,
            {
                "title": title,
                "platform": platform,
                "feed_url": target,
            },
        )
    except Exception as exc:
        if "already exists" in str(exc):
            return {"text": "⚠️ 您已经订阅过这个 RSS 源了。", "ui": {}}
        return {"text": f"❌ 订阅失败: {exc}", "ui": {}}

    try:
        await increment_stat(int(ctx.message.user.id), "subscriptions_added")
    except Exception:
        pass

    return {
        "text": (
            "✅ **RSS 订阅已创建**\n\n"
            f"ID：`{created['id']}`\n"
            f"标题：{created['title']}\n"
            "Bot 将每 30 分钟检查一次更新。"
        ),
        "ui": {},
    }


async def list_subs_command(ctx: UnifiedContext) -> dict:
    subs = await list_subscriptions(ctx.message.user.id)
    if not subs:
        return {"text": "📭 您当前没有任何 RSS 订阅。", "ui": {}}

    msg = "📋 **您的订阅列表**\n\n"
    for sub in subs:
        msg += (
            f"• `#{sub['id']}` [RSS] **{sub['title']}**\n"
            f"  {sub['feed_url']}\n\n"
        )

    msg += "使用 `/rss remove <订阅ID>` 取消订阅。"

    actions = []
    temp_row = []
    for sub in subs:
        label = f"❌ #{sub['id']}"
        temp_row.append({"text": label, "callback_data": f"unsub_{sub['id']}"})
        if len(temp_row) == 3:
            actions.append(temp_row)
            temp_row = []
    if temp_row:
        actions.append(temp_row)

    return {"text": msg, "ui": {"actions": actions}}


async def refresh_user_subscriptions(ctx: UnifiedContext) -> str:
    user_id = ctx.message.user.id

    if ctx.platform_ctx:
        try:
            await ctx.platform_ctx.bot.send_chat_action(
                chat_id=ctx.message.chat.id, action="typing"
            )
        except Exception:
            pass

    from core.scheduler import trigger_manual_rss_check

    result_text = await trigger_manual_rss_check(user_id)
    if result_text:
        return result_text
    return "✅ 检查完成，当前 RSS 订阅没有新增内容。"


async def show_unsubscribe_menu(ctx: UnifiedContext) -> dict:
    subs = await list_subscriptions(ctx.message.user.id)
    if not subs:
        return {"text": "📭 您当前没有任何 RSS 订阅。", "ui": {}}

    actions = []
    for sub in subs:
        label = f"❌ #{sub['id']} {sub['title']}"
        actions.append([{"text": label[:28], "callback_data": f"unsub_{sub['id']}"}])
    actions.append([{"text": "🚫 取消", "callback_data": "unsub_cancel"}])

    return {"text": "📋 **请选择要取消的订阅**：", "ui": {"actions": actions}}


async def remove_subscription_by_target(ctx: UnifiedContext, target: str) -> dict:
    raw = str(target or "").strip()
    if not raw:
        return {"text": "用法: `/rss remove <订阅ID>`", "ui": {}}

    try:
        sub_id = int(raw.lstrip("#"))
    except ValueError:
        return {"text": "❌ `/rss remove` 现在只支持按订阅 ID 删除。", "ui": {}}

    sub = await get_subscription(ctx.message.user.id, sub_id)
    if sub is None:
        return {"text": f"❌ 未找到订阅 `#{sub_id}`。", "ui": {}}
    success = await delete_subscription(ctx.message.user.id, sub_id)
    if not success:
        return {"text": f"❌ 删除失败，订阅 `#{sub_id}` 可能已不存在。", "ui": {}}
    return {
        "text": f"✅ 已取消订阅 `#{sub_id}`：{sub['title']}",
        "ui": {},
    }


async def handle_unsubscribe_callback(ctx: UnifiedContext):
    data = ctx.callback_data
    if not data:
        return

    await ctx.answer_callback()

    if data == "unsub_cancel":
        return "👌 已取消操作。"

    try:
        sub_id = int(str(data).replace("unsub_", ""))
    except ValueError:
        return "❌ 无效的操作。"

    success = await delete_subscription(ctx.callback_user_id, sub_id)
    if success:
        return f"✅ 已取消订阅 `#{sub_id}`。"
    return "❌ 取消失败，订阅可能已不存在。"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RSS subscribe skill CLI bridge.")
    add_common_arguments(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List subscriptions")

    add_parser = subparsers.add_parser("add", help="Add an RSS feed subscription")
    add_parser.add_argument("target", help="RSS URL")

    remove_parser = subparsers.add_parser("remove", help="Remove a subscription")
    remove_parser.add_argument("target", help="Subscription ID")

    subparsers.add_parser("refresh", help="Refresh subscriptions now")
    return parser


def _params_from_args(args: argparse.Namespace) -> dict:
    command = str(args.command or "").strip().lower()
    if command == "list":
        return merge_params(args, {"action": "list"})
    if command == "add":
        return merge_params(args, {"action": "add", "url": str(args.target or "").strip()})
    if command == "remove":
        return merge_params(
            args, {"action": "remove", "target": str(args.target or "").strip()}
        )
    if command == "refresh":
        return merge_params(args, {"action": "refresh"})
    raise SystemExit(f"unsupported command: {command}")


if __name__ == "__main__":
    run_execute_cli(
        _build_parser(),
        _params_from_args,
        execute,
    )
