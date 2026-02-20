import os
import sys

sys.path.append(os.path.join(os.getcwd(), "src"))

from core.markdown_memory_store import markdown_memory_store


def verify_memory_store() -> None:
    user_id = "debug-memory-check"
    ok, detail = markdown_memory_store.remember(
        user_id,
        "请记住我住在江苏无锡，称呼我为主人",
        source="verify_script",
    )
    print(f"remember_ok={ok}, detail={detail}")

    snapshot = markdown_memory_store.load_snapshot(
        user_id,
        include_daily=True,
        max_chars=2000,
    )
    print("snapshot:")
    print(snapshot or "(empty)")


if __name__ == "__main__":
    verify_memory_store()
