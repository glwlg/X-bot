"""
功能需求收集 handlers
"""

import os
import re
import logging
import datetime
from core.platform.models import UnifiedContext
from .base_handlers import check_permission_unified, CONVERSATION_END
from core.config import (
    WAITING_FOR_FEATURE_INPUT,
)

logger = logging.getLogger(__name__)

FEATURE_STATE_KEY = "feature_request"


async def feature_command(ctx: UnifiedContext) -> int:
    """处理 /feature 命令，收集功能需求"""
    if not await check_permission_unified(ctx):
        return CONVERSATION_END

    if not ctx.platform_ctx:
        return CONVERSATION_END

    ctx.user_data.pop(FEATURE_STATE_KEY, None)

    args = ctx.platform_ctx.args
    if args:
        return await process_feature_request(ctx, " ".join(args))

    await ctx.reply(
        "💡 **提交功能需求**\n\n请描述您希望 Bot 拥有的新功能。\n\n发送 /cancel 取消。"
    )
    return WAITING_FOR_FEATURE_INPUT


async def handle_feature_input(ctx: UnifiedContext) -> int:
    """处理需求的交互式输入（支持多轮补充）"""
    text = ctx.message.text
    if not text:
        await ctx.reply("请发送有效文本。")
        return WAITING_FOR_FEATURE_INPUT

    if not ctx.platform_ctx:
        return CONVERSATION_END

    state = ctx.user_data.get(FEATURE_STATE_KEY)
    if state and state.get("filepath"):
        return await append_feature_supplement(ctx, text)
    else:
        return await process_feature_request(ctx, text)


async def save_feature_command(ctx: UnifiedContext) -> int:
    """保存需求并结束对话"""
    if not ctx.platform_ctx:
        return CONVERSATION_END

    state = ctx.user_data.pop(FEATURE_STATE_KEY, None)

    if state and state.get("filename"):
        await ctx.reply(f"✅ 需求 `{state['filename']}` 已保存！")
    else:
        await ctx.reply("✅ 需求收集已结束。")

    return CONVERSATION_END


async def process_feature_request(ctx: UnifiedContext, description: str) -> int:
    """整理用户需求并保存"""
    from core.config import (
        get_client_for_model,
        DATA_DIR,
    )  # lazy import to avoid top level issues if moved
    from core.model_config import get_current_model
    from services.openai_adapter import generate_text

    msg = await ctx.reply("🤔 正在整理您的需求...")

    prompt = f"""用户提出了一个功能需求，请整理成简洁的需求描述。

用户原话：{description}

请按以下格式输出（Markdown），保持简洁：

# [2-6个字的标题]

## 需求描述
1-2 句话描述用户想要什么

## 功能要点
- 要点1
- 要点2（如有）
"""

    try:
        model_to_use = get_current_model()
        client_to_use = get_client_for_model(model_to_use, is_async=True)
        if client_to_use is None:
            raise RuntimeError("OpenAI async client is not initialized")
        doc_content = await generate_text(
            async_client=client_to_use,
            model=model_to_use,
            contents=prompt,
        )
        doc_content = str(doc_content or "").strip()

        title_match = re.search(r"^#\s*(.+)$", doc_content, re.MULTILINE)
        title = title_match.group(1).strip()[:15] if title_match else "需求"
        title_safe = re.sub(r'[\\/*?:"<>|]', "", title).replace(" ", "_")

        timestamp = datetime.datetime.now()
        meta = f"\n\n---\n*提交时间：{timestamp.strftime('%Y-%m-%d %H:%M')} | 用户：{ctx.message.user.id}*"
        doc_content += meta

        feature_dir = os.path.join(DATA_DIR, "feature_requests")
        os.makedirs(feature_dir, exist_ok=True)

        date_str = timestamp.strftime("%Y%m%d")
        existing = [f for f in os.listdir(feature_dir) if f.startswith(date_str)]
        seq = len(existing) + 1
        filename = f"{date_str}_{seq:02d}_{title_safe}.md"
        filepath = os.path.join(feature_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(doc_content)

        if ctx.platform_ctx:
            ctx.user_data[FEATURE_STATE_KEY] = {
                "filepath": filepath,
                "filename": filename,
            }

        await ctx.edit_message(
            getattr(msg, "message_id", getattr(msg, "id", None)),
            f"📝 **需求已记录**\n\n"
            f"📄 `{filename}`\n\n"
            f"{doc_content}\n\n"
            "---\n继续补充说明，或点击 /save_feature 保存结束。",
        )
        return WAITING_FOR_FEATURE_INPUT

    except Exception as e:
        logger.error(f"Feature request error: {e}")
        await ctx.edit_message(
            getattr(msg, "message_id", getattr(msg, "id", None)), f"❌ 处理失败：{e}"
        )
        return CONVERSATION_END


async def append_feature_supplement(ctx: UnifiedContext, supplement: str) -> int:
    """追加用户补充信息到需求文档"""
    state = ctx.user_data.get(FEATURE_STATE_KEY, {}) if ctx.platform_ctx else {}
    filepath = state.get("filepath")
    filename = state.get("filename")

    if not filepath:
        return CONVERSATION_END

    msg = await ctx.reply("📝 正在更新需求...")

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        timestamp = datetime.datetime.now().strftime("%H:%M")
        supplement_section = f"\n\n## 补充说明 ({timestamp})\n{supplement}"

        if "---\n*提交时间" in content:
            parts = content.rsplit("---\n*提交时间", 1)
            content = (
                parts[0].rstrip() + supplement_section + "\n\n---\n*提交时间" + parts[1]
            )
        else:
            content += supplement_section

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        await ctx.edit_message(
            getattr(msg, "message_id", getattr(msg, "id", None)),
            f"✅ **补充已添加**\n\n"
            f"📄 `{filename}`\n\n"
            "继续补充说明，或点击 /save_feature 保存结束。",
        )
        return WAITING_FOR_FEATURE_INPUT

    except Exception as e:
        logger.error(f"Append feature error: {e}")
        await ctx.edit_message(
            getattr(msg, "message_id", getattr(msg, "id", None)), f"❌ 更新失败：{e}"
        )
        return CONVERSATION_END
