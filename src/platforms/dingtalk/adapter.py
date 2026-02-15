"""
DingTalk Platform Adapter

使用 dingtalk-stream SDK 实现 Stream 模式接入。
"""

import logging
import asyncio
import re
from typing import Any, Optional, Callable, Dict, List, Tuple, Union

from core.platform.adapter import BotAdapter
from core.platform.models import UnifiedContext, UnifiedMessage
from core.platform.exceptions import (
    MediaDownloadUnavailableError,
    MessageSendError,
)

from .mapper import map_chatbot_message
from .formatter import markdown_to_dingtalk_compat

logger = logging.getLogger(__name__)


def create_chatbot_handler(adapter: "DingTalkAdapter"):
    """
    创建钉钉机器人消息处理器
    必须继承自 dingtalk_stream.ChatbotHandler
    """
    import dingtalk_stream

    class DingTalkChatbotHandler(dingtalk_stream.ChatbotHandler):
        """钉钉机器人消息处理器"""

        def __init__(self, adapter_ref: "DingTalkAdapter", logger_ref=None):
            super().__init__()
            self.adapter = adapter_ref
            if logger_ref:
                self.logger = logger_ref

        def pre_start(self):
            """Lifecycle hook called by client before start"""
            pass

        async def process(self, callback: dingtalk_stream.CallbackMessage):
            """处理收到的消息回调"""
            try:
                incoming_message = dingtalk_stream.ChatbotMessage.from_dict(
                    callback.data
                )
                self.logger.info(
                    f"DingTalk received message: {incoming_message.text.content if incoming_message.text else 'N/A'}"
                )

                # Dispatch to main loop to avoid "Future attached to a different loop" errors
                if self.adapter._main_loop:
                    asyncio.run_coroutine_threadsafe(
                        self.adapter._handle_incoming_message(incoming_message),
                        self.adapter._main_loop,
                    )
                else:
                    # Fallback (risky if cross-thread objects are used)
                    await self.adapter._handle_incoming_message(incoming_message)

                return dingtalk_stream.AckMessage.STATUS_OK, "OK"
            except Exception as e:
                self.logger.error(
                    f"Error processing DingTalk message: {e}", exc_info=True
                )
                return dingtalk_stream.AckMessage.STATUS_SYSTEM_EXCEPTION, str(e)

    return DingTalkChatbotHandler(adapter, logger)


class DingTalkAdapter(BotAdapter):
    """
    DingTalk Platform Adapter using dingtalk-stream SDK
    """

    def __init__(self, client_id: str, client_secret: str):
        super().__init__("dingtalk")
        self.client_id = client_id
        self.client_secret = client_secret

        # 延迟初始化 SDK 客户端
        self._client = None
        self._credential = None

        # Handler registries
        self._message_handler: Optional[Callable[[UnifiedContext], Any]] = None
        self._command_handlers: Dict[str, Callable[[UnifiedContext], Any]] = {}
        self._callback_handlers: List[
            Tuple[re.Pattern, Callable[[UnifiedContext], Any]]
        ] = []

        # User Data Storage (In-Memory)
        self._user_data_store: Dict[str, Dict[str, Any]] = {}

        # 存储回复上下文 (用于 reply_text)
        self._reply_contexts: Dict[str, Any] = {}

        # Capture main event loop for thread-safe scheduling
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._main_loop = None
            logger.warning("DingTalkAdapter initialized without a running event loop!")

    @property
    def can_update_message(self) -> bool:
        """DingTalk does not support editing messages"""
        return False

    def _init_client(self):
        """初始化钉钉 Stream 客户端"""
        if self._client is not None:
            return

        try:
            import dingtalk_stream

            self._credential = dingtalk_stream.Credential(
                self.client_id, self.client_secret
            )
            self._client = dingtalk_stream.DingTalkStreamClient(self._credential)

            # 注册消息处理器
            handler = create_chatbot_handler(self)
            self._client.register_callback_handler(
                dingtalk_stream.ChatbotMessage.TOPIC, handler
            )

            logger.info("✅ DingTalk Stream Client initialized")
        except ImportError:
            raise ImportError(
                "dingtalk-stream package not installed. Run: uv add dingtalk-stream"
            )

    async def _handle_incoming_message(self, incoming_message):
        """
        处理收到的钉钉消息
        支持"伪回调"：如果文本匹配已注册的回调模式，则视为回调事件
        """
        try:
            # 1. 转换为 UnifiedMessage
            unified_msg = map_chatbot_message(incoming_message)

            # 2. 保存回复上下文 (用于 reply_text)
            self._reply_contexts[unified_msg.chat.id] = incoming_message

            # 3. 创建 Initial Context
            context = UnifiedContext(
                message=unified_msg,
                platform_event=incoming_message,
                platform_ctx=self._client,
                _adapter=self,
            )

            text = unified_msg.text or ""

            # 4. 检查是否是命令 (优先处理)
            if text.startswith("/"):
                parts = text.split(" ", 1)
                command = parts[0][1:]  # 去掉斜杠
                if command in self._command_handlers:
                    logger.info(f"DingTalk: Dispatching command /{command}")
                    await self._command_handlers[command](context)
                    return

            # 5. Pseudo-Callback Logic (Prefix-based)
            # 只有以 d_cb: 开头的文本才被视为回调
            if text.startswith("d_cb:"):
                # 提取真实 callback_data
                real_callback_data = text[5:]  # remove 'd_cb:'

                logger.info(
                    f"DingTalk: Processing pseudo-callback: {real_callback_data}"
                )

                # 寻找匹配的 Handler
                for pattern, handler in self._callback_handlers:
                    if pattern.search(real_callback_data):
                        # 构造带有 callback_data 的 Context
                        class CallbackUnifiedContext(UnifiedContext):
                            @property
                            def callback_data(self):
                                return self._cb_data

                        cb_ctx = CallbackUnifiedContext(
                            message=unified_msg,
                            platform_event=incoming_message,
                            platform_ctx=self._client,
                            _adapter=self,
                        )
                        cb_ctx._cb_data = real_callback_data

                        await handler(cb_ctx)
                        return

                logger.warning(
                    f"DingTalk: No handler found for callback: {real_callback_data}"
                )
                return

            # 6. 默认消息处理器
            if self._message_handler:
                await self._message_handler(context)

        except Exception as e:
            logger.error(f"Error handling DingTalk message: {e}", exc_info=True)

    async def start(self) -> None:
        """启动适配器"""
        self._init_client()

        # 使用 asyncio.create_task 非阻塞启动
        asyncio.create_task(self._run_client())
        logger.info("🚀 DingTalk Adapter started (Stream Mode)")

    async def _run_client(self):
        """运行钉钉客户端"""
        try:
            # dingtalk-stream 的 start() 是阻塞的
            # 需要在后台任务中运行
            await asyncio.to_thread(self._client.start_forever)
        except Exception as e:
            logger.error(f"DingTalk client error: {e}", exc_info=True)

    async def stop(self) -> None:
        """停止适配器"""
        # dingtalk-stream SDK 目前没有提供显式的 stop 方法
        logger.info("DingTalk Adapter stopping...")

    async def reply_text(
        self, context: UnifiedContext, text: str, ui: Optional[Dict] = None, **kwargs
    ) -> Any:
        """回复文本消息"""
        try:
            from dingtalk_stream import ChatbotMessage

            # 格式化文本
            text = markdown_to_dingtalk_compat(text)

            # 获取原始消息对象
            incoming_message = context.platform_event
            if not isinstance(incoming_message, ChatbotMessage):
                # 尝试从缓存获取
                incoming_message = self._reply_contexts.get(context.message.chat.id)

            if incoming_message:
                # 使用 SDK 的 ChatbotHandler 方法
                # ChatbotMessage 本身没有 reply 方法，必须通过 Handler 调用
                from dingtalk_stream import ChatbotHandler

                handler = ChatbotHandler()
                handler.dingtalk_client = self._client

                # 构建 Markdown 消息
                if ui and ui.get("actions"):
                    # 如果有按钮，使用 ActionCard
                    await self._reply_action_card(
                        handler, incoming_message, text, ui.get("actions")
                    )
                else:
                    # 普通 Markdown 消息
                    await self._reply_markdown(handler, incoming_message, text)

            return True
        except Exception as e:
            logger.error(f"DingTalk reply_text error: {e}", exc_info=True)
            raise MessageSendError(str(e))

    async def _reply_markdown(self, handler, incoming_message, text: str):
        """发送 Markdown 消息"""
        try:
            # 运行在线程池中，避免阻塞主循环
            await asyncio.to_thread(
                handler.reply_markdown,
                title="X-Bot Reply",
                text=text,
                incoming_message=incoming_message,
            )
            logger.info("DingTalk markdown reply sent via SessionWebhook")
        except Exception as e:
            logger.error(f"DingTalk _reply_markdown error: {e}", exc_info=True)
            raise

    async def _send_webhook_message(self, url: str, payload: Dict):
        """使用 httpx 直接发送 Webhook 消息"""
        import httpx

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, timeout=10)
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"Webhook send failed: {e}")
            raise

    async def _reply_action_card(
        self, handler, incoming_message, text: str, actions: List[List[Dict]]
    ):
        """发送 ActionCard 消息"""
        try:
            # 展平按钮列表 (ActionCard 只支持一维数组，但在视觉上我们可以利用排列做文章，但API限制了)
            # DingTalk ActionCard btns is a flat list.
            # We map: text -> title, url -> actionURL.
            # callbacks -> actionURL to "dtmd://dingtalkclient/sendMessage?content={callback}"

            flat_btns = []
            for row in actions:
                for btn in row:
                    title = btn.get("text", "Button")
                    url = btn.get("url")
                    cb = btn.get("callback_data")

                    action_url = url
                    if not action_url and cb:
                        # 构造伪回调链接
                        # 使用 urllib.parse.quote 编码内容
                        import urllib.parse

                        # Add 'd_cb:' prefix to mark this as a callback
                        prefixed_cb = f"d_cb:{cb}"
                        encoded_cb = urllib.parse.quote(prefixed_cb)

                        action_url = (
                            f"dtmd://dingtalkclient/sendMessage?content={encoded_cb}"
                        )

                    if action_url:
                        flat_btns.append({"title": title, "actionURL": action_url})

            # 构造 ActionCard Payload
            # Markdown 不支持部分 Markdown 语法，但支持基本的
            payload = {
                "msgtype": "actionCard",
                "actionCard": {
                    "title": text.split("\n")[0][:20] + "...",  # 简略标题
                    "text": text,
                    "btnOrientation": "0",  # 0: 竖版 (容纳更多按钮), 1: 横版
                    "btns": flat_btns,
                },
            }

            if (
                hasattr(incoming_message, "sessionWebhook")
                and incoming_message.sessionWebhook
            ):
                await self._send_webhook_message(
                    incoming_message.sessionWebhook, payload
                )
                logger.info("DingTalk ActionCard sent via SessionWebhook")
            else:
                # Fallback to Markdown if no webhook URL (shouldn't happen in Stream mode)
                await self._reply_markdown(handler, incoming_message, text)

        except Exception as e:
            logger.error(f"DingTalk _reply_action_card error: {e}", exc_info=True)
            # Fallback
            from .formatter import markdown_to_dingtalk_compat

            await self._reply_markdown(handler, incoming_message, text)

    async def _send_message_api(
        self, conversation_id: str, text: str, receiver_id: str = None
    ):
        """通过 API 发送消息 (用于主动推送)"""
        # 注意: 钉钉机器人主动发送消息需要使用 OpenAPI
        # Stream 模式主要用于接收消息，发送消息仍需调用 REST API
        logger.warning(
            "DingTalk proactive message sending requires OpenAPI (not implemented)"
        )

    async def send_message(self, chat_id: Union[int, str], text: str, **kwargs) -> Any:
        """主动发送消息 (用于调度器推送)"""
        # 钉钉主动发消息需要使用 OpenAPI，这里记录日志
        logger.warning(
            f"DingTalk proactive messaging to {chat_id} not fully implemented"
        )
        return None

    async def edit_text(
        self,
        context: UnifiedContext,
        message_id: str,
        text: str,
        ui: Optional[Dict] = None,
        **kwargs,
    ) -> Any:
        """编辑消息 - 钉钉不支持，fallback 为发送新消息"""
        logger.info("DingTalk does not support message editing, sending new message")
        return await self.reply_text(context, text, ui=ui, **kwargs)

    async def reply_photo(
        self,
        context: UnifiedContext,
        photo: Union[str, bytes],
        caption: Optional[str] = None,
        **kwargs,
    ) -> Any:
        """回复图片"""
        try:
            if isinstance(photo, str) and photo.startswith("http"):
                text = f"![image]({photo})"
                if caption:
                    text = f"{caption}\n\n{text}"
            else:
                # bytes 需要上传，暂不实现
                logger.warning("DingTalk photo upload from bytes not implemented; fallback to text.")
                text = caption or "⚠️ 当前钉钉通道暂不支持二进制图片上传。"
            return await self.reply_text(context, text)
        except Exception as e:
            logger.error(f"DingTalk reply_photo error: {e}")
            raise MessageSendError(str(e))

    async def reply_video(
        self,
        context: UnifiedContext,
        video: Union[str, bytes],
        caption: Optional[str] = None,
        **kwargs,
    ) -> Any:
        """回复视频 - 钉钉支持有限，发送链接"""
        try:
            if isinstance(video, str) and video.startswith("http"):
                text = f"🎬 视频链接: {video}"
                if caption:
                    text = f"{caption}\n\n{text}"
            else:
                logger.warning("DingTalk video upload from bytes not implemented")
                text = caption or "⚠️ 当前钉钉通道暂不支持二进制视频上传。"
            return await self.reply_text(context, text)
        except Exception as e:
            logger.error(f"DingTalk reply_video error: {e}")
            raise MessageSendError(str(e))

    async def reply_audio(
        self,
        context: UnifiedContext,
        audio: Union[str, bytes],
        caption: Optional[str] = None,
        **kwargs,
    ) -> Any:
        """回复音频"""
        logger.warning("DingTalk audio reply not fully implemented")
        text = caption or "⚠️ 当前钉钉通道暂不支持二进制音频上传。"
        return await self.reply_text(context, text)

    async def reply_document(
        self,
        context: UnifiedContext,
        document: Union[str, bytes],
        filename: Optional[str] = None,
        caption: Optional[str] = None,
        **kwargs,
    ) -> Any:
        """回复文档"""
        try:
            if isinstance(document, str) and document.startswith("http"):
                text = f"📄 文件下载: [{filename or 'document'}]({document})"
                if caption:
                    text = f"{caption}\n\n{text}"
            else:
                logger.warning("DingTalk document upload from bytes not implemented; fallback to text.")
                text = caption or "⚠️ 当前钉钉通道暂不支持二进制文档上传。"
            return await self.reply_text(context, text)
        except Exception as e:
            logger.error(f"DingTalk reply_document error: {e}")
            raise MessageSendError(str(e))

    async def delete_message(
        self,
        context: UnifiedContext,
        message_id: str,
        chat_id: Optional[str] = None,
        **kwargs,
    ) -> Any:
        """删除消息 - 钉钉不支持"""
        logger.info("DingTalk does not support message deletion")
        return False

    async def send_chat_action(
        self,
        context: UnifiedContext,
        action: str,
        chat_id: Optional[str] = None,
        **kwargs,
    ) -> Any:
        """发送 Chat Action (typing 等) - 钉钉不支持"""
        # 钉钉没有 typing indicator
        return None

    async def download_file(
        self, context: UnifiedContext, file_id: str, **kwargs
    ) -> bytes:
        """下载文件"""
        candidate = str(file_id or "").strip()
        file_url = str(getattr(context.message, "file_url", "") or "").strip()

        try:
            if candidate.startswith("http://") or candidate.startswith("https://"):
                return await self._download_by_url(candidate)
            if file_url.startswith("http://") or file_url.startswith("https://"):
                return await self._download_by_url(file_url)

            # Best-effort OpenAPI resolution for downloadCode/fileId.
            if candidate:
                resolved_url = await self._resolve_download_url(candidate)
                if resolved_url:
                    return await self._download_by_url(resolved_url)
        except Exception as exc:
            raise MediaDownloadUnavailableError(
                "DingTalk file download failed during transfer."
            ) from exc

        raise MediaDownloadUnavailableError(
            "DingTalk file download unavailable for this message. "
            "Please resend as a direct URL or use Telegram/Discord for media analysis."
        )

    async def _download_by_url(self, url: str) -> bytes:
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content

    async def _resolve_download_url(self, download_code: str) -> Optional[str]:
        import httpx

        token = await self._fetch_openapi_access_token()
        if not token:
            return None

        endpoint = "https://api.dingtalk.com/v1.0/robot/messageFiles/download"
        payload = {"downloadCode": download_code}
        headers = {"x-acs-dingtalk-access-token": token}

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(endpoint, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
                for key in ("downloadUrl", "download_url", "url"):
                    value = data.get(key)
                    if isinstance(value, str) and value.startswith(("http://", "https://")):
                        return value
        except Exception as exc:
            logger.warning("DingTalk resolve download URL failed: %s", exc)
            return None

        return None

    async def _fetch_openapi_access_token(self) -> Optional[str]:
        import httpx

        endpoint = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
        payload = {
            "appKey": self.client_id,
            "appSecret": self.client_secret,
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(endpoint, json=payload)
                response.raise_for_status()
                data = response.json()
                token = data.get("accessToken")
                if isinstance(token, str) and token:
                    return token
        except Exception as exc:
            logger.warning("DingTalk access token fetch failed: %s", exc)
            return None

        return None

    def on_command(
        self,
        command: str,
        handler: Callable[[UnifiedContext], Any],
        description: str = None,
        **kwargs,
    ):
        """注册命令处理器"""
        self._command_handlers[command] = handler
        logger.info(f"Registered DingTalk command: /{command}")

    def on_message(self, filters_obj: Any, handler_func: Callable):
        """注册消息处理器 (兼容接口)"""
        # DingTalk 不使用 filter，直接设置消息处理器
        self._message_handler = handler_func
        logger.info("Registered DingTalk message handler")

    def register_message_handler(self, handler: Callable[[UnifiedContext], Any]):
        """注册消息处理器"""
        self._message_handler = handler
        logger.info("Registered DingTalk message handler")

    def on_callback_query(self, pattern: str, handler: Callable[[UnifiedContext], Any]):
        """注册回调处理器"""
        compiled = re.compile(pattern)
        self._callback_handlers.append((compiled, handler))
        logger.info(f"Registered DingTalk callback pattern: {pattern}")

    def get_user_data(self, user_id: str) -> Dict[str, Any]:
        """获取用户数据"""
        if user_id not in self._user_data_store:
            self._user_data_store[user_id] = {}
        return self._user_data_store[user_id]
