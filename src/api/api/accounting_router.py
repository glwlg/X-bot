from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import csv
import io
from datetime import datetime

from api.core.database import get_async_session
from api.auth.users import current_active_user
from api.auth.models import User
from api.models.accounting import Book, Account, Category, Record

router = APIRouter()


@router.get("/records")
async def get_records(
    book_id: int,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    # Verify book ownership/access (simplified: just ownership)
    book = await session.get(Book, book_id)
    if not book or book.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized or book not found")

    result = await session.execute(
        select(Record)
        .where(Record.book_id == book_id)
        .order_by(Record.record_time.desc())
    )
    records = result.scalars().all()
    return records


@router.post("/import/csv")
async def import_csv(
    book_id: int,
    file: UploadFile = File(...),
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    # Verify book
    book = await session.get(Book, book_id)
    if not book or book.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized or book not found")

    contents = await file.read()
    decoded = contents.decode("utf-8-sig")  # typical for Excel CSVs

    csv_reader = csv.DictReader(io.StringIO(decoded))

    # 缓存 accounts/categories 避免重复查库
    accounts_cache = {}

    async def get_or_create_account(name: str):
        if not name:
            return None
        if name in accounts_cache:
            return accounts_cache[name]
        res = await session.execute(
            select(Account).where(Account.book_id == book_id, Account.name == name)
        )
        acc = res.scalars().first()
        if not acc:
            acc = Account(book_id=book_id, name=name)
            session.add(acc)
            await session.flush()
        accounts_cache[name] = acc
        return acc

    categories_cache = {}

    async def get_or_create_category(name: str, ctype: str):
        if not name:
            return None
        key = f"{name}_{ctype}"
        if key in categories_cache:
            return categories_cache[key]
        res = await session.execute(
            select(Category).where(
                Category.book_id == book_id,
                Category.name == name,
                Category.type == ctype,
            )
        )
        cat = res.scalars().first()
        if not cat:
            cat = Category(book_id=book_id, name=name, type=ctype)
            session.add(cat)
            await session.flush()
        categories_cache[key] = cat
        return cat

    records = []
    for row in csv_reader:
        # csv format based on sample: 类型(type), 货币, 金额(amount), 汇率, 项目(book_ignored), 分类(category), 父类(parent_cat_ignored), 账户(account_ignored), 付款(from_acc), 收款(to_acc), 商家(payee), 地址, 日期(record_time), 标签, 作者, 备注(remark)
        rtype = row.get("类型", "支出")
        amount_str = row.get("金额", "0").replace("¥", "").replace(",", "").strip()
        try:
            amount = float(amount_str)
        except:
            continue

        date_str = row.get("日期", "")
        record_time = datetime.utcnow()
        if date_str:
            try:
                # expecting "2026/2/21 19:23"
                record_time = datetime.strptime(date_str, "%Y/%m/%d %H:%M")
            except:
                pass

        remark = row.get("备注", "")
        payee = row.get("商家", "")
        cat_name = row.get("分类", "未分类")

        from_acc_name = row.get("付款", "")
        to_acc_name = row.get("收款", "")

        cat = await get_or_create_category(cat_name, rtype)
        from_acc = await get_or_create_account(from_acc_name)
        to_acc = await get_or_create_account(to_acc_name)

        rec = Record(
            book_id=book_id,
            type=rtype,
            amount=amount,
            account_id=from_acc.id if from_acc else None,
            target_account_id=to_acc.id if to_acc else None,
            category_id=cat.id if cat else None,
            record_time=record_time,
            payee=payee[:100],
            remark=remark[:500],
            creator_id=user.id,
        )
        records.append(rec)

    session.add_all(records)
    await session.commit()

    return {"message": f"Successfully imported {len(records)} records."}
