"""
功能需求收集 handlers
"""
import os
import re
import logging
import datetime
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from core.config import WAITING_FOR_FEATURE_INPUT, gemini_client, GEMINI_MODEL, DATA_DIR
from .base_handlers import check_permission
from utils import smart_edit_text, smart_reply_text

logger = logging.getLogger(__name__)

FEATURE_STATE_KEY = "feature_request"


async def feature_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理 /feature 命令，收集功能需求"""
    if not await check_permission(update):
        return ConversationHandler.END

    context.user_data.pop(FEATURE_STATE_KEY, None)
    
    args = context.args
    if args:
        return await process_feature_request(update, context, " ".join(args))
        
    await smart_reply_text(update,
        "💡 **提交功能需求**\n\n"
        "请描述您希望 Bot 拥有的新功能。\n\n"
        "发送 /cancel 取消。"
    )
    return WAITING_FOR_FEATURE_INPUT


async def handle_feature_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理需求的交互式输入（支持多轮补充）"""
    text = update.message.text
    if not text:
        await update.message.reply_text("请发送有效文本。")
        return WAITING_FOR_FEATURE_INPUT
    
    state = context.user_data.get(FEATURE_STATE_KEY)
    if state and state.get("filepath"):
        return await append_feature_supplement(update, context, text)
    else:
        return await process_feature_request(update, context, text)


async def save_feature_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """保存需求并结束对话"""
    state = context.user_data.pop(FEATURE_STATE_KEY, None)
    
    if state and state.get("filename"):
        await smart_reply_text(update, f"✅ 需求 `{state['filename']}` 已保存！")
    else:
        await smart_reply_text(update, "✅ 需求收集已结束。")
    
    return ConversationHandler.END


async def process_feature_request(update: Update, context: ContextTypes.DEFAULT_TYPE, description: str) -> int:
    """整理用户需求并保存"""
    msg = await smart_reply_text(update, "🤔 正在整理您的需求...")
    
    prompt = f'''用户提出了一个功能需求，请整理成简洁的需求描述。

用户原话：{description}

请按以下格式输出（Markdown），保持简洁：

# [2-6个字的标题]

## 需求描述
1-2 句话描述用户想要什么

## 功能要点
- 要点1
- 要点2（如有）
'''

    try:
        response = await gemini_client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        doc_content = response.text.strip()
        
        title_match = re.search(r'^#\s*(.+)$', doc_content, re.MULTILINE)
        title = title_match.group(1).strip()[:15] if title_match else "需求"
        title_safe = re.sub(r'[\\/*?:"<>|]', '', title).replace(' ', '_')
        
        timestamp = datetime.datetime.now()
        meta = f"\n\n---\n*提交时间：{timestamp.strftime('%Y-%m-%d %H:%M')} | 用户：{update.effective_user.id}*"
        doc_content += meta
        
        feature_dir = os.path.join(DATA_DIR, "feature_requests")
        os.makedirs(feature_dir, exist_ok=True)
        
        date_str = timestamp.strftime("%Y%m%d")
        existing = [f for f in os.listdir(feature_dir) if f.startswith(date_str)]
        seq = len(existing) + 1
        filename = f"{date_str}_{seq:02d}_{title_safe}.md"
        filepath = os.path.join(feature_dir, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(doc_content)
        
        context.user_data[FEATURE_STATE_KEY] = {
            "filepath": filepath,
            "filename": filename,
        }
        
        await smart_edit_text(msg,
            f"📝 **需求已记录**\n\n"
            f"📄 `{filename}`\n\n"
            f"{doc_content}\n\n"
            "---\n继续补充说明，或点击 /save_feature 保存结束。"
        )
        return WAITING_FOR_FEATURE_INPUT
        
    except Exception as e:
        logger.error(f"Feature request error: {e}")
        await smart_edit_text(msg, f"❌ 处理失败：{e}")
        return ConversationHandler.END


async def append_feature_supplement(update: Update, context: ContextTypes.DEFAULT_TYPE, supplement: str) -> int:
    """追加用户补充信息到需求文档"""
    state = context.user_data.get(FEATURE_STATE_KEY, {})
    filepath = state.get("filepath")
    filename = state.get("filename")
    
    if not filepath:
        return ConversationHandler.END
    
    msg = await smart_reply_text(update, "📝 正在更新需求...")
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        timestamp = datetime.datetime.now().strftime('%H:%M')
        supplement_section = f"\n\n## 补充说明 ({timestamp})\n{supplement}"
        
        if "---\n*提交时间" in content:
            parts = content.rsplit("---\n*提交时间", 1)
            content = parts[0].rstrip() + supplement_section + "\n\n---\n*提交时间" + parts[1]
        else:
            content += supplement_section
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        
        await smart_edit_text(msg,
            f"✅ **补充已添加**\n\n"
            f"📄 `{filename}`\n\n"
            "继续补充说明，或点击 /save_feature 保存结束。"
        )
        return WAITING_FOR_FEATURE_INPUT
        
    except Exception as e:
        logger.error(f"Append feature error: {e}")
        await smart_edit_text(msg, f"❌ 更新失败：{e}")
        return ConversationHandler.END
