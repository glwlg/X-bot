import logging
import shlex

from sqlalchemy import select

from core.accounting_store import get_active_book_id, set_active_book_id
from core.platform.models import UnifiedContext
from api.core.database import get_session_maker
from api.models.accounting import Book, Record
from api.models.binding import PlatformUserBinding
from .base_handlers import check_permission_unified

logger = logging.getLogger(__name__)


def _parse_subcommand(text: str) -> tuple[str, str]:
    raw = (text or "").strip()
    if not raw:
        return "help", ""
    parts = raw.split(maxsplit=2)
    if not parts:
        return "help", ""
    if not parts[0].startswith("/acc"):
        return "help", ""
    if len(parts) == 1:
        return "info", ""
    cmd = parts[1].strip().lower()
    args = parts[2].strip() if len(parts) >= 3 else ""
    return cmd, args


def _accounting_usage_text() -> str:
    return (
        "📊 记账助手用法:\n\n"
        "`/acc info` - 查看当前记账账本和简要统计\n"
        "`/acc list` - 列出你名下的所有账本\n"
        "`/acc use <账本ID/名称>` - 切换默认记账账本\n"
        "`/acc record <文字/发图>` - 快捷记账支持\n"
        "`/acc help` - 帮助\n\n"
        "💡 Tip: 也可以直接发送带有消费信息的图片或文字，Bot会自动帮你记账。"
    )


async def _get_user_id_from_binding(platform: str, platform_user_id: str) -> int | None:
    session_maker = get_session_maker()
    async with session_maker() as session:
        stmt = select(PlatformUserBinding).where(
            PlatformUserBinding.platform == platform,
            PlatformUserBinding.platform_user_id == platform_user_id,
        )
        binding = (await session.execute(stmt)).scalars().first()
        if binding:
            return binding.user_id
        return None


async def accounting_command(ctx: UnifiedContext) -> None:
    if not await check_permission_unified(ctx):
        return

    platform = ctx.message.platform or "telegram"
    platform_user_id = str(ctx.message.user.id)
    text = ctx.message.text or ""

    sub, args = _parse_subcommand(text)

    if sub in {"help", "h", "?"}:
        await ctx.reply(_accounting_usage_text())
        return

    # Check binding first
    user_id = await _get_user_id_from_binding(platform, platform_user_id)
    if not user_id:
        await ctx.reply(
            f"❌ 您还未绑定网页端账号。请先绑定。您的 ID：`{platform_user_id}`, 平台：`{platform}`"
        )
        return

    session_maker = get_session_maker()

    if sub in {"info", "i"}:
        active_book_id = await get_active_book_id(user_id)
        async with session_maker() as session:
            # 找到所有的账本
            stmt = select(Book).where(Book.owner_id == user_id)
            books = (await session.execute(stmt)).scalars().all()

            if not books:
                await ctx.reply("❌ 您还未创建任何账本，请先在系统内创建一个账本。")
                return

            book = None
            if active_book_id is not None:
                book = next((b for b in books if b.id == active_book_id), None)

            if not book:
                book = books[0]
                await set_active_book_id(user_id, book.id)

            await ctx.reply(
                f"📈 当前记账账本：**{book.name}**\n\n您可以使用 `/acc list` 切换其他记账账本。"
            )
        return

    if sub in {"list", "ls"}:
        active_book_id = await get_active_book_id(user_id)
        async with session_maker() as session:
            stmt = select(Book).where(Book.owner_id == user_id)
            books = (await session.execute(stmt)).scalars().all()

            if not books:
                await ctx.reply("❌ 您还未创建任何账本，请先在系统内创建一个账本。")
                return

            # fallback
            if active_book_id is None:
                active_book_id = books[0].id
                await set_active_book_id(user_id, books[0].id)

            lines = ["📚 您的账本列表："]
            for b in books:
                marker = "👉" if b.id == active_book_id else "  "
                lines.append(f"{marker} `{b.id}` | **{b.name}**")
            lines.append("\n使用 `/acc use <ID>` 来切换账本。")
            await ctx.reply("\n".join(lines))
        return

    if sub == "use":
        target = args.strip()
        if not target:
            await ctx.reply("用法: `/acc use <账本ID或名称>`")
            return

        async with session_maker() as session:
            stmt = select(Book).where(Book.owner_id == user_id)
            books = (await session.execute(stmt)).scalars().all()

            if not books:
                await ctx.reply("❌ 您还未创建任何账本，请先在系统内创建一个账本。")
                return

            found_book = None
            # Try ID exact match first
            if target.isdigit():
                found_book = next((b for b in books if b.id == int(target)), None)

            # Try name match
            if not found_book:
                found_book = next((b for b in books if b.name == target), None)

            if not found_book:
                await ctx.reply(f"❌ 找不到名为或 ID 为 `{target}` 的账本。")
                return

            await set_active_book_id(user_id, found_book.id)
            await ctx.reply(f"✅ 当前记账账本已切换为：**{found_book.name}**")
        return

    if sub == "record":
        # Using dispatch_tools directly via quick_accounting could be done here,
        # but the request itself is typically sent using normal interaction to trigger it.
        # Alternatively, let the orchestration handle it. Let's just encourage simple reply if empty.
        if not args:
            await ctx.reply("直接在后面输入信息即可，或发送带有收支金额的截图。")
        else:
            await ctx.reply(
                "提示: 此指令可以配合大模型智能截取参数。对于强制单步记录，请使用普通的语言描述。您甚至无需加 `/acc record`。"
            )
        return

    await ctx.reply(_accounting_usage_text())
