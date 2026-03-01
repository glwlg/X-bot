from __future__ import annotations

import asyncio
import logging
import os

from worker.kernel.daemon import run_worker_kernel


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    )
    asyncio.run(run_worker_kernel())
