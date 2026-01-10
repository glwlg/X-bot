"""
使用统计模块
记录和展示用户使用情况
"""
"""
使用统计模块
记录和展示用户使用情况
"""
import logging
from database import increment_stat as db_increment_stat, get_user_stats as db_get_user_stats

logger = logging.getLogger(__name__)


async def increment_stat(user_id: int, stat_name: str, count: int = 1) -> None:
    """
    增加用户统计计数
    
    Args:
        user_id: 用户 ID
        stat_name: 统计项名称 (downloads, ai_chats, image_generations, etc.)
        count: 增加的数量
    """
    await db_increment_stat(user_id, stat_name, count)


async def get_user_stats_text(user_id: int) -> str:
    """获取用户统计信息的格式化文本"""
    stats = await db_get_user_stats(user_id)
    
    if not stats:
        return "📊 您还没有使用记录。"
    
    first_use = str(stats.get("first_use", "未知"))[:10]
    last_use = str(stats.get("last_use", "未知"))[:10]
    
    return (
        "📊 **您的使用统计**\n\n"
        f"💬 AI 对话次数：{stats.get('ai_chats', 0)}\n"
        f"🎤 语音对话次数：{stats.get('voice_chats', 0)}\n"
        f"📄 文档分析次数：{stats.get('doc_analyses', 0)}\n"
        f"🌍 翻译消息数：{stats.get('translations_count', 0)}\n"
        f"📹 视频下载次数：{stats.get('downloads', 0)}\n"
        f"🎨 图片生成次数：{stats.get('image_generations', 0)}\n"
        f"🔍 图片分析次数：{stats.get('photo_analyses', 0)}\n"
        f"🎬 视频分析次数：{stats.get('video_analyses', 0)}\n"
        f"📝 视频摘要次数：{stats.get('video_summaries', 0)}\n"
        f"⏰ 设置提醒次数：{stats.get('reminders_set', 0)}\n"
        f"📢 添加订阅次数：{stats.get('subscriptions_added', 0)}\n\n"
        f"📅 首次使用：{first_use}\n"
        f"📅 最近使用：{last_use}"
    )


# 全局统计需要数据库支持，暂时简化或后续在 database.py 添加聚合查询
# 目前先只保留个人统计功能
def get_global_stats_text() -> str:
    return "📊 全局统计功能正在升级中..."

