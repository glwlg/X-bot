import sys
import os
import asyncio

sys.path.append(os.path.join(os.getcwd(), "src"))
from api.core.database import get_session_maker
from api.models.accounting import Account
from sqlalchemy import select


async def main():
    session_maker = get_session_maker()
    async with session_maker() as session:
        result = await session.execute(select(Account))
        accounts = result.scalars().all()
        for a in accounts:
            print(f"{a.id} | {a.type} | {a.name} | balance: {a.balance}")


if __name__ == "__main__":
    asyncio.run(main())
