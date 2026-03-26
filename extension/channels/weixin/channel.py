from __future__ import annotations

from core.config import (
    WEIXIN_BASE_URL,
    WEIXIN_CDN_BASE_URL,
    WEIXIN_ENABLE,
    WEIXIN_LOGIN_POLL_INTERVAL_SEC,
    WEIXIN_LOGIN_TIMEOUT_SEC,
    WEIXIN_TEXT_CHUNK_LIMIT,
    is_user_admin,
)
from core.extension_base import ChannelExtension
from core.runtime_config_store import runtime_config_store

from .adapter import WeixinAdapter
from ..common import COMMON_CALLBACK_PATTERN, button_callback, route_message_by_type


def _usage_text() -> str:
    return (
        "用法:\n"
        "`/wxbind qr` - 生成新的微信绑定二维码\n"
        "`/wxbind list` - 查看已绑定微信用户\n"
        "`/wxbind help` - 查看帮助"
    )


def _parse_subcommand(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return "help"
    parts = raw.split(maxsplit=2)
    if not parts or not parts[0].startswith("/wxbind"):
        return "help"
    if len(parts) == 1:
        return "help"
    return str(parts[1] or "").strip().lower() or "help"


class WeixinChannelExtension(ChannelExtension):
    name = "weixin_channel"
    platform_name = "weixin"
    priority = 40

    def __init__(self) -> None:
        self.adapter = None

    def enabled(self, runtime) -> bool:
        _ = runtime
        return bool(WEIXIN_ENABLE) and runtime_config_store.is_platform_enabled(
            "weixin",
            default=True,
        )

    async def _cmd_wxbind(self, ctx):
        user = getattr(getattr(ctx, "message", None), "user", None)
        user_id = str(getattr(user, "id", "") or "").strip()
        if not is_user_admin(user_id):
            await ctx.reply("⛔ 仅管理员可使用 `/wxbind`。")
            return

        adapter = self.adapter
        if adapter is None:
            await ctx.reply("⛔ 微信适配器未启用，无法使用 `/wxbind`。")
            return

        action = _parse_subcommand(ctx.message.text or "")
        if action in {"help", "h", "?"}:
            await ctx.reply(_usage_text())
            return

        if action == "list":
            rows = adapter.list_bound_users()
            if not rows:
                await ctx.reply("当前没有已记录的微信绑定用户。")
                return
            lines = ["已绑定微信用户：", ""]
            for item in rows:
                lines.append(
                    f"- `{item.get('user_id')}` | {item.get('status') or 'active'} | "
                    f"bot={item.get('account_id') or '-'} | "
                    f"{item.get('source') or '-'} | {item.get('bound_at') or '-'}"
                )
            await ctx.reply("\n".join(lines))
            return

        if action == "qr":
            requester_platform = str(getattr(ctx.message, "platform", "") or "").strip().lower()
            requester_chat_id = str(
                getattr(getattr(ctx.message, "chat", None), "id", "") or ""
            ).strip()
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
                await ctx.reply("❌ 未能生成二维码，请稍后重试。")
                return

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
                    return
                except Exception:
                    pass
            await ctx.reply(f"{caption}\n\n二维码链接：{qr_content}")
            return

        await ctx.reply(_usage_text())

    def register(self, runtime) -> None:
        self.adapter = runtime.register_adapter(
            WeixinAdapter(
                base_url=WEIXIN_BASE_URL,
                cdn_base_url=WEIXIN_CDN_BASE_URL,
                login_timeout_sec=WEIXIN_LOGIN_TIMEOUT_SEC,
                login_poll_interval_sec=WEIXIN_LOGIN_POLL_INTERVAL_SEC,
                text_chunk_limit=WEIXIN_TEXT_CHUNK_LIMIT,
            )
        )
        self.adapter.register_message_handler(route_message_by_type)
        self.adapter.on_callback_query(COMMON_CALLBACK_PATTERN, button_callback)

        platforms = [name for name in ("telegram", "weixin") if runtime.has_adapter(name)]
        if platforms:
            runtime.register_command(
                "wxbind",
                self._cmd_wxbind,
                platforms=platforms,
                description="微信多绑定",
            )
