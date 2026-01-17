"""
查询天气 Skill - 查询指定城市天气
"""
import httpx
import urllib.parse
from telegram import Update
from telegram.ext import ContextTypes
from utils import smart_reply_text

SKILL_META = {
    "name": "weather",
    "description": "查询指定城市的天气情况",
    "triggers": ["天气", "weather", "查天气", "气温"],
    "params": {
        "location": {
            "type": "str",
            "description": "城市名称，例如：北京"
        }
    },
    "version": "1.0.0",
    "author": "X-Bot-Generator"
}

async def execute(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> None:
    # 获取位置参数，如果未提供则默认为空
    location = params.get("location") or (context.args[0] if context.args else None)

    if not location:
        await smart_reply_text(update, "❓ 请提供城市名称，例如：天气 北京")
        return

    # URL 编码用户输入，防止注入和格式错误
    encoded_location = urllib.parse.quote(location)
    
    # 使用 wttr.in 格式化输出: %l(地点) %c(图标) %t(温度) %h(湿度) %w(风)
    # 确保 URL 中不包含换行符
    url = f"https://wttr.in/{encoded_location}?format=%l:+%c+%t+湿度:%h+风向:%w&lang=zh"

    try:
        # 设置超时时间，避免长时间挂起
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            
            if response.status_code == 200:
                weather_info = response.text.strip()
                if not weather_info:
                    await smart_reply_text(update, f"❌ 未找到城市 '{location}' 的天气信息。")
                else:
                    await smart_reply_text(update, f"🌤 {weather_info}")
            elif response.status_code == 404:
                await smart_reply_text(update, f"❌ 找不到城市: {location}")
            else:
                await smart_reply_text(update, f"⚠️ 获取天气失败，服务返回状态码: {response.status_code}")

    except httpx.TimeoutException:
        await smart_reply_text(update, "⏰ 请求超时，请稍后再试。")
    except httpx.RequestError as e:
        await smart_reply_text(update, f"🚫 网络请求错误: {str(e)}")
    except Exception as e:
        await smart_reply_text(update, f"💥 发生未预期的错误: {str(e)}")