
import logging
import json
from enum import Enum
from typing import Optional, Dict, Any

from config import gemini_client, ROUTING_MODEL

logger = logging.getLogger(__name__)

class UserIntent(Enum):
    DOWNLOAD_VIDEO = "download_video"
    GENERATE_IMAGE = "generate_image"
    SET_REMINDER = "set_reminder"
    RSS_SUBSCRIBE = "rss_subscribe"
    MONITOR_KEYWORD = "monitor_keyword"
    GENERAL_CHAT = "general_chat"
    UNKNOWN = "unknown"

async def analyze_intent(text: str) -> Dict[str, Any]:
    """
    Analyze user text to determine intent.
    
    Returns a dict with:
    - intent: UserIntent value
    - params: dict of extracted parameters (e.g., {"url": "..."})
    """
    if not text:
        return {"intent": UserIntent.UNKNOWN, "params": {}}

    try:
        # Prompt for intent classification
        system_instruction = (
            "You are X-Bot's Smart Router. Your job is to classify user messages into actions.\n\n"
            
            "## 1. X-Bot Capabilities (For Context Only)\n"
            "X-Bot can do many things. Some map to specific intents, others fall back to general chat.\n"
            "- **Video/Audio Download**: `download_video`\n"
            "- **AI Image Generation**: `generate_image`\n"
            "- **Reminders**: `set_reminder`\n"
            "- **RSS Subscription**: `rss_subscribe`\n"
            "- **Keyword Monitor**: `monitor_keyword`\n"
            "- **AI Chat/Translation**: `general_chat`\n\n"

            "## 2. Intent Rules\n"
            "### A. download_video\n"
            "Trigger: User EXPLICITLY wants to download/save video/audio from a URL.\n"
            "Keywords: download, save, get, 视频, 下载, 保存.\n"
            "Params: `url` (valid URL)\n"
            "Example: '下载这个 https://...' -> { 'intent': 'download_video', 'params': { 'url': '...' } }\n\n"

            "### B. generate_image\n"
            "Trigger: User wants to draw/generate an image.\n"
            "Keywords: draw, generate image, 画, 生成图片.\n"
            "Params: `prompt` (description)\n"
            "Example: '画一只猫' -> { 'intent': 'generate_image', 'params': { 'prompt': '一只猫' } }\n\n"

            "### C. set_reminder\n"
            "Trigger: User wants to set a timer or reminder.\n"
            "Keywords: remind, timer, alarm, 提醒, 定时.\n"
            "Params: `time` (e.g. '10m', '1h', '30s'), `content` (what to remind).\n"
            "Example: '10分钟后提醒我喝水' -> { 'intent': 'set_reminder', 'params': { 'time': '10m', 'content': '喝水' } }\n"
            "Example: 'remind me to sleep in 1h' -> { 'intent': 'set_reminder', 'params': { 'time': '1h', 'content': 'sleep' } }\n\n"

            "### D. rss_subscribe\n"
            "Trigger: User wants to subscribe to an RSS feed.\n"
            "Keywords: subscribe, rss, 订阅.\n"
            "Params: `url` (RSS feed URL)\n"
            "Example: '订阅这个RSS https://...' -> { 'intent': 'rss_subscribe', 'params': { 'url': 'https://...' } }\n\n"

            "### E. monitor_keyword\n"
            "Trigger: User wants to monitor a keyword for news.\n"
            "Keywords: monitor, track, keyword, 监控, 监听.\n"
            "Params: `keyword` (text to monitor)\n"
            "Example: '监控关键词 AI News' -> { 'intent': 'monitor_keyword', 'params': { 'keyword': 'AI News' } }\n\n"
            
            "### F. general_chat (Default)\n"
            "Everything else falls here: Questions, small talk, summaries, commands like /start.\n"
            "Example: '这个视频讲了什么 https://...' -> { 'intent': 'general_chat' }\n\n"
            
            "## 3. Output Format\n"
            "Return JSON only: {\"intent\": \"...\", \"params\": {...}}"
        )

        response = gemini_client.models.generate_content(
            model=ROUTING_MODEL,
            contents=text,
            config={
                "system_instruction": system_instruction,
                "response_mime_type": "application/json",
            },
        )
        
        # Robust JSON extraction
        import re
        text_response = response.text
        if not text_response:
             logger.warning("Empty response from intent router")
             return {"intent": UserIntent.GENERAL_CHAT, "params": {}}

        # Clean markdown code blocks
        clean_text = re.sub(r"```json|```", "", text_response).strip()
        
        # Try to find JSON object if there's extra text
        match = re.search(r"\{.*\}", clean_text, re.DOTALL)
        if match:
            clean_text = match.group(0)

        try:
            result = json.loads(clean_text)
            intent_str = result.get("intent", "general_chat")
            params = result.get("params", {})
            
            # Map string to Enum (normalized)
            try:
                intent = UserIntent(intent_str.lower())
            except ValueError:
                intent = UserIntent.GENERAL_CHAT
                
            logger.info(f"Intent analysis: {intent} params={params}")
            return {"intent": intent, "params": params}
        except json.JSONDecodeError as e:
            logger.error(f"JSON parsing failed for router response: '{text_response}'. Error: {e}")
            return {"intent": UserIntent.GENERAL_CHAT, "params": {}}
            
    except Exception as e:
        logger.error(f"Intent analysis unexpected error: {e}")
        return {"intent": UserIntent.GENERAL_CHAT, "params": {}}
        
    # Fallback
    return {"intent": UserIntent.GENERAL_CHAT, "params": {}}
