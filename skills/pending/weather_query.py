"""
天气查询 Skill - 查询指定城市天气
"""
import httpx
from telegram import Update
from telegram.ext import ContextTypes
from utils import smart_reply_text

SKILL_META = {
    "name": "weather_query",
    "description": "查询全球任意城市的天气情况",
    "triggers": ["天气", "查天气", "气温", "weather"],
    "params": {
        "city": {
            "type": "str",
            "description": "想要查询的城市名称",
            "required": True
        }
    },
    "version": "1.0.1",
    "author": "X-Bot-Generator"
}

async def execute(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> None:
    # 获取城市参数
    city = params.get("city")
    
    # 如果参数中没有提取到城市，尝试从原始文本中获取（容错处理）
    if not city and context.args:
        city = " ".join(context.args)
    
    if not city:
        await smart_reply_text(update, "❓ 请提供城市名称，例如：天气 北京")
        return

    # 发送等待提示
    await smart_reply_text(update, f"🔍 正在查询 {city} 的天气...")

    try:
        # 使用 wttr.in 服务，format=3 为简洁模式，lang=zh-cn 强制中文
        url = f"https://wttr.in/{city}"
        params_http = {
            "format": "3",
            "lang": "zh-cn"
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params_http, timeout=10.0)
            
            if response.status_code == 200:
                weather_info = response.text.strip()
                # 简单的结果验证
                if "Unknown location" in weather_info:
                    await smart_reply_text(update, f"❌ 未找到城市：{city}")
                else:
                    await smart_reply_text(update, f"🌤 天气报告：\n{weather_info}")
            else:
                await smart_reply_text(update, "❌ 天气服务暂时不可用")
                
    except httpx.TimeoutException:
        await smart_reply_text(update, "⏰ 查询超时，请稍后重试")
    except Exception as e:
        await smart_reply_text(update, f"❌ 查询出错: {str(e)}")