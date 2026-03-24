"""
Ikaros 主程序入口
"""

from __future__ import annotations

import asyncio
import logging
import signal

from core.config import (
    CORE_CHAT_EXECUTION_MODE,
    HEARTBEAT_ENABLED,
    HEARTBEAT_MODE,
    LOG_LEVEL,
)
from core.extension_runtime import init_extension_runtime
from core.heartbeat_worker import heartbeat_worker
from core.long_term_memory import long_term_memory
from core.platform.registry import adapter_manager
from core.subagent_supervisor import subagent_supervisor
from extension.channels.registry import channel_registry
from extension.memories.registry import memory_registry
from extension.plugins.registry import plugin_registry
from extension.skills.registry import skill_registry

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=getattr(logging, LOG_LEVEL, logging.INFO),
)

logger = logging.getLogger(__name__)


async def init_services():
    logger.info("⚡ Initializing global services...")
    try:
        from core.state_store import init_db

        await init_db()
        logger.info("✅ Repository store initialized.")

        from core.scheduler import (
            scheduler,
            load_jobs_from_db,
            start_dynamic_skill_scheduler,
        )

        logger.info("⚡ Starting schedulers...")
        scheduler.start()
        await load_jobs_from_db()

        runtime = init_extension_runtime(scheduler=scheduler)
        memory_registry.activate_extension(runtime)
        await long_term_memory.initialize()

        skill_registry.scan_skills()
        logger.info("Loaded %s skills", len(skill_registry.get_skill_index()))

        channel_registry.register_extensions(runtime)
        skill_registry.register_extensions(runtime)
        plugin_registry.register_extensions(runtime)

        start_dynamic_skill_scheduler()
        logger.info("✅ Schedulers and extensions started.")

        from core.audit_store import audit_store
        from core.kernel_config_store import kernel_config_store
        from core.task_inbox import task_inbox

        await task_inbox.compact_storage()
        audit_store.maintain()

        kernel_config_store.snapshot(
            {
                "core_chat_execution_mode": CORE_CHAT_EXECUTION_MODE,
                "heartbeat_enabled": HEARTBEAT_ENABLED,
                "heartbeat_mode": HEARTBEAT_MODE,
                "memory_provider": long_term_memory.get_provider_name(),
            },
            actor="bootstrap",
            reason="init_services_snapshot",
        )
        return runtime
    except Exception as exc:
        logger.error("❌ Error in init_services: %s", exc, exc_info=True)
        raise


async def main():
    logger.info("Starting Ikaros (Extension Runtime Mode)...")
    runtime = await init_services()
    await heartbeat_worker.start()

    stop_event = asyncio.Event()

    def signal_handler(signum, frame):
        _ = frame
        logger.info("Signal %s received, stopping...", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        await runtime.run_startup()
        await adapter_manager.start_all()
        await subagent_supervisor.start()
        logger.info("All adapters started. Press Ctrl+C to stop.")
        await stop_event.wait()
    except Exception as exc:
        logger.error("Fatal error: %s", exc, exc_info=True)
    finally:
        logger.info("Shutting down...")
        await runtime.run_shutdown()
        await subagent_supervisor.stop()
        await heartbeat_worker.stop()
        await adapter_manager.stop_all()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
