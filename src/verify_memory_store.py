import os
import sys
import asyncio

sys.path.append(os.path.join(os.getcwd(), "src"))

from core.long_term_memory import long_term_memory


async def verify_memory_store() -> None:
    user_id = "debug-memory-check"
    await long_term_memory.initialize()
    ok, detail = await long_term_memory.remember_user(
        user_id,
        "请记住我住在江苏无锡，称呼我为主人",
        source="verify_script",
    )
    print(f"remember_ok={ok}, detail={detail}")

    snapshot = await long_term_memory.load_user_snapshot(
        user_id,
        include_daily=True,
        max_chars=2000,
    )
    print("snapshot:")
    print(snapshot or "(empty)")


if __name__ == "__main__":
    asyncio.run(verify_memory_store())
