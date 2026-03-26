from __future__ import annotations

import logging

from core.extension_base import PluginExtension
from handlers import (
    chatlog_command,
    compact_command,
    feature_command,
    handle_chatlog_callback,
    handle_compact_callback,
    handle_feature_input,
    handle_heartbeat_callback,
    handle_home_callback,
    handle_model_callback,
    handle_new_command,
    handle_task_callback,
    handle_usage_callback,
    heartbeat_command,
    help_command,
    model_command,
    save_feature_command,
    start,
    stop_command,
    task_command,
    usage_command,
)

logger = logging.getLogger(__name__)


class CoreCommandsPlugin(PluginExtension):
    name = "core_commands"
    priority = 10

    def register(self, runtime) -> None:
        runtime.register_command("start", start, description="显示主菜单")
        runtime.register_command("new", handle_new_command, description="开启新对话")
        runtime.register_command("help", help_command, description="使用帮助")
        runtime.register_command("chatlog", chatlog_command, description="检索对话记录")
        runtime.register_command("compact", compact_command, description="压缩当前对话")
        runtime.register_command("stop", stop_command, description="停止当前任务")
        runtime.register_command("heartbeat", heartbeat_command, description="管理心跳")
        runtime.register_command("task", task_command, description="查看 ikaros 任务")
        runtime.register_command("model", model_command, description="查看和切换模型")
        runtime.register_command("usage", usage_command, description="查看 LLM 用量")

        runtime.register_callback("^home_", handle_home_callback)
        runtime.register_callback("^helpm_", handle_home_callback)
        runtime.register_callback("^hbm_", handle_heartbeat_callback)
        runtime.register_callback("^taskm_", handle_task_callback)
        runtime.register_callback("^model_", handle_model_callback)
        runtime.register_callback("^usagem_", handle_usage_callback)
        runtime.register_callback("^chatlog_", handle_chatlog_callback)
        runtime.register_callback("^compact_", handle_compact_callback)

        if not runtime.has_adapter("telegram"):
            return

        try:
            from telegram.ext import ConversationHandler, filters

            from core.config import WAITING_FOR_FEATURE_INPUT
            from handlers import cancel

            tg_adapter = runtime.get_adapter("telegram")
            tg_app = getattr(tg_adapter, "application", None)
            if tg_app is None:
                return

            feature_conv_handler = ConversationHandler(
                entry_points=[tg_adapter.create_command_handler("feature", feature_command)],
                states={
                    WAITING_FOR_FEATURE_INPUT: [
                        tg_adapter.create_command_handler(
                            "save_feature",
                            save_feature_command,
                        ),
                        tg_adapter.create_message_handler(
                            filters.TEXT & ~filters.COMMAND,
                            handle_feature_input,
                        ),
                    ],
                },
                fallbacks=[
                    tg_adapter.create_command_handler("cancel", cancel),
                    tg_adapter.create_command_handler("save_feature", save_feature_command),
                ],
                per_message=False,
            )
            tg_app.add_handler(feature_conv_handler)
        except Exception:
            logger.warning("Failed to install Telegram feature flow.", exc_info=True)
