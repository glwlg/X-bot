import asyncio
import aiosqlite
import os

DB_PATH = "data/bot_data.db"


from repositories.base import init_db


async def verify_schema():
    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}")
        # Even if not found, init_db should create it

    print("Running init_db()...")
    await init_db()

    async with aiosqlite.connect(DB_PATH) as db:
        print("Checking 'watchlist' table schema...")
        async with db.execute("PRAGMA table_info(watchlist)") as cursor:
            columns = await cursor.fetchall()
            found_platform = False
            for col in columns:
                print(f"Column: {col[1]}, Type: {col[2]}")
                if col[1] == "platform":
                    found_platform = True

            if found_platform:
                print("✅ 'platform' column found in 'watchlist' table.")
            else:
                print(
                    "❌ 'platform' column NOT found in 'watchlist' table. Migration might not have run yet."
                )

        print("\nChecking 'subscriptions' table schema...")
        async with db.execute("PRAGMA table_info(subscriptions)") as cursor:
            columns = await cursor.fetchall()
            for col in columns:
                if col[1] == "platform":
                    print("✅ 'platform' column found in 'subscriptions' table.")


if __name__ == "__main__":
    asyncio.run(verify_schema())
