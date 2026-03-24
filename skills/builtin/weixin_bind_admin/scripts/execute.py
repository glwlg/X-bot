from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SRC_ROOT = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.config import is_user_admin
from core.platform.models import UnifiedContext
from core.platform.registry import adapter_manager
from core.skill_cli import (
    add_common_arguments,
    merge_params,
    prepare_default_env,
    run_execute_cli,
)

prepare_default_env(REPO_ROOT)


def _parse_subcommand(text: str) -> tuple[str, str]:
    raw = str(text or "").strip()
    if not raw:
        return "help", ""
    parts = raw.split(maxsplit=2)
    if not parts or not parts[0].startswith("/wxbind"):
        return "help", ""
    if len(parts) == 1:
        return "help", ""
    sub = str(parts[1] or "").strip().lower()
    args = str(parts[2] if len(parts) >= 3 else "").strip()
    return sub, args


def _usage_text() -> str:
    return (
        "用法:\n"
        "`/wxbind qr` - 生成新的微信绑定二维码\n"
        "`/wxbind list` - 查看已绑定微信用户\n"
        "`/wxbind help` - 查看帮助"
    )


def _resolve_weixin_adapter():
    try:
        return adapter_manager.get_adapter("weixin")
    except Exception:
        return None


async def execute(ctx: UnifiedContext, params: dict, runtime=None) -> dict:
    user = getattr(getattr(ctx, "message", None), "user", None)
    user_id = str(getattr(user, "id", "") or "").strip()
    if not is_user_admin(user_id):
        return {"text": "⛔ 仅管理员可使用 `/wxbind`。", "ui": {}}

    adapter = _resolve_weixin_adapter()
    if adapter is None:
        return {"text": "⛔ 微信适配器未启用，无法使用 `/wxbind`。", "ui": {}}

    action = str(params.get("action") or "help").strip().lower()
    if action in {"help", "h", "?"}:
        return {"text": _usage_text(), "ui": {}}

    if action == "list":
        rows = adapter.list_bound_users()
        if not rows:
            return {"text": "当前没有已记录的微信绑定用户。", "ui": {}}
        lines = ["已绑定微信用户：", ""]
        for item in rows:
            lines.append(
                f"- `{item.get('user_id')}` | {item.get('status') or 'active'} | "
                f"bot={item.get('account_id') or '-'} | "
                f"{item.get('source') or '-'} | {item.get('bound_at') or '-'}"
            )
        return {"text": "\n".join(lines), "ui": {}}

    if action == "qr":
        requester_platform = str(getattr(ctx.message, "platform", "") or "").strip().lower()
        requester_chat_id = str(getattr(getattr(ctx.message, "chat", None), "id", "") or "").strip()
        requester_account_id = str(
            ((getattr(ctx.message, "raw_data", None) or {}).get("to_user_id") or "")
        ).strip()
        payload = await adapter.start_additional_binding(
            requester_user_id=user_id,
            requester_account_id=requester_account_id,
            notification_platform=requester_platform,
            notification_chat_id=requester_chat_id or user_id,
        )
        qr_content = str(payload.get("qr_content") or payload.get("qr_url") or "").strip()
        caption = (
            "请让对方扫码完成微信绑定。\n"
            "扫码成功后，我会自动把该微信加入 allow-list，并回消息通知你。"
        )
        if not qr_content:
            return {"text": "❌ 未能生成二维码，请稍后重试。", "ui": {}}

        qr_png = b""
        render_qr_png = getattr(adapter, "render_qr_png", None)
        if callable(render_qr_png):
            qr_png = bytes(render_qr_png(qr_content) or b"")

        if qr_png:
            try:
                await ctx.reply_photo(
                    qr_png,
                    caption=caption,
                    filename="weixin-bind-qr.png",
                )
                return {}
            except Exception:
                pass
        return {"text": f"{caption}\n\n二维码链接：{qr_content}", "ui": {}}

    return {"text": _usage_text(), "ui": {}}


def register_handlers(manager):
    async def cmd_wxbind(ctx):
        sub, _args = _parse_subcommand(ctx.message.text or "")
        return await execute(ctx, {"action": sub})

    for platform in ("telegram", "weixin"):
        try:
            adapter = manager.get_adapter(platform)
        except Exception:
            continue
        if platform == "telegram":
            adapter.on_command(
                "wxbind",
                cmd_wxbind,
                description="微信多绑定",
                group=-2,
            )
        else:
            adapter.on_command("wxbind", cmd_wxbind, description="微信多绑定")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Weixin bind admin skill.")
    add_common_arguments(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("help", help="Show help")
    subparsers.add_parser("list", help="List bound users")
    subparsers.add_parser("qr", help="Create a binding QR code")
    return parser


def _params_from_args(args: argparse.Namespace) -> dict:
    command = str(args.command or "help").strip().lower()
    return merge_params(args, {"action": command})


async def _run() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return await run_execute_cli(execute, args=args, params=_params_from_args(args))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
