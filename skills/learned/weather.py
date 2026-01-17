"""
查询天气 Skill - 获取指定城市的实时天气信息
"""
import httpx
from telegram import Update
from telegram.ext import ContextTypes
from utils import smart_reply_text

SKILL_META = {
    "name": "weather",
    "description": "查询全球城市实时天气",
    "triggers": ["天气", "weather", "气温", "查天气"],
    "params": {
        "location": {
            "type": "str",
            "description": "城市名称，如：北京、Shanghai"
        }
    },
    "version": "1.0.1",
    "author": "X-Bot-Generator"
}


async def execute(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> None:
    # 获取用户 ID 用于日志或逻辑隔离（本例仅作展示）
    user_id = update.effective_user.id
    
    # 优先从解析的 params 中获取位置，如果没有则尝试从原始参数获取
    location = params.get("location")
    if not location and context.args:
        location = " ".join(context.args)
        
    if not location:
        await smart_reply_text(update, "请提供要查询的城市名称，例如：\n天气 北京")
        return

    # 使用 wttr.in 公共服务，无需 Key
    # format参数说明: %l:地点, %c:天气图标, %t:温度, %h:湿度, %w:风速
    # lang=zh 强制中文显示
    target_url = f"https://wttr.in/{location}?format=%l:\n%c+%t\n💦+湿度:+%h\n🌬+风速:+%w&m&lang=zh"

    try:
        await smart_reply_text(update, f"🔍 正在查询 {location} 的天气...")
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(target_url)
            
            if response.status_code == 200:
                weather_info = response.text.strip()
                # 简单的错误检查，wttr.in 如果找不到城市通常会返回很长的 HTML 或特定的 Help 文本
                if "Unknown location" in weather_info or len(weather_info) > 1000:
                    await smart_reply_text(update, f"❌ 未找到城市 '{location}'，请检查拼写。")
                else:
                    await smart_reply_text(update, f"🌤 天气报告:\n{weather_info}")
            else:
                await smart_reply_text(update, f"❌ 查询失败 (HTTP {response.status_code})，请稍后再试。")
                
    except httpx.RequestError as e:
        await smart_reply_text(update, f"❌ 网络请求错误: {str(e)}")
    except Exception as e:
        await smart_reply_text(update, f"❌ 发生未知错误: {str(e)}")