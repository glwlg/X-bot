from core.platform.models import UnifiedContext
from core.state_store import (
    add_account,
    get_account,
    list_accounts,
    delete_account,
)

try:
    import pyotp
except ImportError:
    pyotp = None


async def execute(ctx: UnifiedContext, params: dict, runtime=None) -> dict:
    """执行账号管理"""
    action = params.get("action", "list")
    service = params.get("service", "").lower().strip()
    data_raw = params.get("data", "")

    # Intelligence: If only service is provided and action is default/unknown, assume 'get'
    # But intent router usually sets action.
    # If using regex skill trigger: "账号 google" -> action='default'? Need better extraction.
    # Assuming params extraction works well or we fallback.

    user_id = ctx.message.user.id

    if action in ["list", "list_all"]:
        accounts = await list_accounts(user_id)
        if not accounts:
            return {"text": "📭 您还没有保存任何账号。"}

        msg = "📋 **已保存的账号**：\n\n"
        for acc in accounts:
            msg += f"• `{acc}`\n"
        msg += "\n发送 `账号 <名称>` 查看详情。"
        # In a real app we might return markup buttons here
        return {"text": msg}

    if action == "get":
        if not service:
            # Try to guess service from data or leftovers
            # But for strictness:
            return {"text": "❌ 请指定要查看的服务名称 (例如: 账号 google)"}

        account = await get_account(user_id, service)
        if not account:
            return {"text": f"❌ 未找到服务 `{service}` 的账号信息。"}

        # Format output
        msg = f"🔐 **{service}**\n\n"
        mfa_code = ""

        for k, v in account.items():
            if k == "mfa_secret":
                # Generate TOTP if pyotp is available
                if pyotp and v:
                    try:
                        totp = pyotp.TOTP(v.replace(" ", ""))
                        mfa_code = totp.now()
                        msg += f"**MFA Code**: `{mfa_code}` (有效期 30s)\n"
                    except Exception as e:
                        msg += f"**MFA Secret**: `{v}` (生成失败: {e})\n"
                else:
                    msg += f"**{k}**: `{v}`\n"
            else:
                msg += f"**{k}**: `{v}`\n"

        # Auto-copyable version for MFA
        if mfa_code:
            msg += f"\n点击复制 MFA: `{mfa_code}`"

        return {"text": msg}

    if action == "add":
        if not service:
            return {"text": "❌ 请指定服务名称 (service=xxx)"}
        if not data_raw:
            return {"text": "❌ 请提供账号数据 (data=... 或 key=value)"}

        # Parse data
        # Support JSON or key=value string
        import json

        parsed_data = {}
        try:
            parsed_data = json.loads(data_raw)
        except Exception:
            # Try key=value parsing
            pairs = data_raw.split()
            for p in pairs:
                if "=" in p:
                    k, v = p.split("=", 1)
                    parsed_data[k] = v
                else:
                    # Treat as raw note?
                    parsed_data["note"] = data_raw
                    break

        if not parsed_data:
            return {"text": "❌ 数据格式无法解析，请使用 key=value 格式。"}

        success = await add_account(user_id, service, parsed_data)
        if success:
            return {"text": f"✅ 账号 `{service}` 已保存。"}
        else:
            return {"text": "❌ 保存失败。"}

    if action == "remove":
        if not service:
            return {"text": "❌ 请指定要删除的服务名称。"}

        success = await delete_account(user_id, service)
        if success:
            return {"text": f"🗑️ 账号 `{service}` 已删除。"}
        else:
            return {"text": "❌ 删除失败。"}

    return {"text": f"❌ 未知操作: {action}"}
