from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, distinct
from pydantic import BaseModel
from typing import Optional
import csv
import io
from datetime import datetime

from api.core.database import get_async_session
from api.auth.users import current_active_user
from api.auth.models import User
from api.models.accounting import Book, Account, Category, Record

router = APIRouter()


# ─── Pydantic Schemas ────────────────────────────────────────────────


class RecordCreate(BaseModel):
    type: str  # 支出/收入/转账
    amount: float
    category_name: str = "未分类"
    account_name: str = ""
    target_account_name: str = ""
    payee: str = ""
    remark: str = ""
    record_time: Optional[str] = None


class AccountCreate(BaseModel):
    name: str
    type: str = "现金"  # 现金/储蓄卡/信用卡
    balance: float = 0


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    balance: Optional[float] = None


# ─── Helper: verify book ownership ───────────────────────────────────


async def _get_book(
    book_id: int,
    user: User,
    session: AsyncSession,
) -> Book:
    book = await session.get(Book, book_id)
    if not book or book.owner_id != user.id:
        raise HTTPException(status_code=403, detail="无权访问该账本")
    return book


# ─── Helper: get or create account/category ──────────────────────────


async def _get_or_create_account(
    session: AsyncSession,
    book_id: int,
    name: str,
    acc_type: str = "现金",
    cache: Optional[dict] = None,
) -> Optional[Account]:
    if not name:
        return None
    if cache is not None and name in cache:
        return cache[name]
    res = await session.execute(
        select(Account).where(Account.book_id == book_id, Account.name == name)
    )
    acc = res.scalars().first()
    if not acc:
        acc = Account(book_id=book_id, name=name, type=acc_type)
        session.add(acc)
        await session.flush()
    if cache is not None:
        cache[name] = acc
    return acc


async def _get_or_create_category(
    session: AsyncSession,
    book_id: int,
    name: str,
    ctype: str,
    cache: Optional[dict] = None,
) -> Optional[Category]:
    if not name:
        return None
    key = f"{name}_{ctype}"
    if cache is not None and key in cache:
        return cache[key]
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
    if cache is not None:
        cache[key] = cat
    return cat


# ─── Books CRUD ──────────────────────────────────────────────────────


@router.get("/books")
async def list_books(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    result = await session.execute(select(Book).where(Book.owner_id == user.id))
    books = result.scalars().all()
    return [{"id": b.id, "name": b.name} for b in books]


@router.post("/books")
async def create_book(
    data: dict,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="账本名称不能为空")
    book = Book(name=name, owner_id=user.id)
    session.add(book)
    await session.commit()
    await session.refresh(book)
    return {"id": book.id, "name": book.name}


# ─── Records CRUD ────────────────────────────────────────────────────


@router.get("/records")
async def get_records(
    book_id: int,
    limit: int = Query(default=50, le=200),
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)
    result = await session.execute(
        select(Record)
        .where(Record.book_id == book_id)
        .order_by(Record.record_time.desc())
        .limit(limit)
    )
    records = result.scalars().all()
    # Enrich with category/account names
    enriched = []
    for r in records:
        cat_name = ""
        if r.category_id:
            cat = await session.get(Category, r.category_id)
            if cat:
                cat_name = cat.name
        acc_name = ""
        if r.account_id:
            acc = await session.get(Account, r.account_id)
            if acc:
                acc_name = acc.name
        target_acc_name = ""
        if r.target_account_id:
            tacc = await session.get(Account, r.target_account_id)
            if tacc:
                target_acc_name = tacc.name
        enriched.append(
            {
                "id": r.id,
                "type": r.type,
                "amount": float(r.amount),
                "category": cat_name,
                "account": acc_name,
                "target_account": target_acc_name,
                "payee": r.payee or "",
                "remark": r.remark or "",
                "record_time": r.record_time.isoformat() if r.record_time else "",
            }
        )
    return enriched


@router.post("/records")
async def create_record(
    book_id: int,
    data: RecordCreate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)

    cat = await _get_or_create_category(session, book_id, data.category_name, data.type)
    from_acc = await _get_or_create_account(session, book_id, data.account_name)
    to_acc = await _get_or_create_account(session, book_id, data.target_account_name)

    record_time = datetime.utcnow()
    if data.record_time:
        try:
            record_time = datetime.fromisoformat(data.record_time)
        except ValueError:
            pass

    rec = Record(
        book_id=book_id,
        type=data.type,
        amount=data.amount,
        account_id=from_acc.id if from_acc else None,
        target_account_id=to_acc.id if to_acc else None,
        category_id=cat.id if cat else None,
        record_time=record_time,
        payee=data.payee[:100] if data.payee else "",
        remark=data.remark[:500] if data.remark else "",
        creator_id=user.id,
    )
    session.add(rec)
    await session.commit()
    await session.refresh(rec)
    return {"id": rec.id, "message": "记录已创建"}


# ─── Statistics ──────────────────────────────────────────────────────


@router.get("/records/summary")
async def records_summary(
    book_id: int,
    year: int,
    month: int,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """月度收支汇总"""
    await _get_book(book_id, user, session)

    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)

    result = await session.execute(
        select(
            Record.type,
            func.sum(Record.amount).label("total"),
        )
        .where(
            Record.book_id == book_id,
            Record.record_time >= start,
            Record.record_time < end,
        )
        .group_by(Record.type)
    )
    rows = result.all()

    income = 0.0
    expense = 0.0
    for row in rows:
        if row.type == "收入":
            income = float(row.total or 0)
        elif row.type == "支出":
            expense = float(row.total or 0)

    return {"income": income, "expense": expense, "balance": income - expense}


@router.get("/records/daily-summary")
async def daily_summary(
    book_id: int,
    year: int,
    month: int,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """按日收支汇总（趋势图用）"""
    await _get_book(book_id, user, session)

    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)

    result = await session.execute(
        select(
            func.strftime("%Y-%m-%d", Record.record_time).label("day"),
            Record.type,
            func.sum(Record.amount).label("total"),
        )
        .where(
            Record.book_id == book_id,
            Record.record_time >= start,
            Record.record_time < end,
        )
        .group_by("day", Record.type)
        .order_by("day")
    )
    rows = result.all()

    daily: dict[str, dict] = {}
    for row in rows:
        d = row.day
        if d not in daily:
            daily[d] = {"date": d, "income": 0.0, "expense": 0.0}
        if row.type == "收入":
            daily[d]["income"] = float(row.total or 0)
        elif row.type == "支出":
            daily[d]["expense"] = float(row.total or 0)

    return list(daily.values())


@router.get("/records/category-summary")
async def category_summary(
    book_id: int,
    year: int,
    month: int,
    type: str = "支出",
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """按分类汇总（统计饼图用）"""
    await _get_book(book_id, user, session)

    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)

    result = await session.execute(
        select(
            Category.name.label("category"),
            func.sum(Record.amount).label("total"),
        )
        .join(Category, Record.category_id == Category.id, isouter=True)
        .where(
            Record.book_id == book_id,
            Record.type == type,
            Record.record_time >= start,
            Record.record_time < end,
        )
        .group_by(Category.name)
        .order_by(func.sum(Record.amount).desc())
    )
    rows = result.all()
    return [
        {"category": r.category or "未分类", "amount": float(r.total or 0)}
        for r in rows
    ]


@router.get("/records/yearly-summary")
async def yearly_summary(
    book_id: int,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """按年汇总（年度统计柱状图用）"""
    await _get_book(book_id, user, session)

    result = await session.execute(
        select(
            func.strftime("%Y", Record.record_time).label("year"),
            Record.type,
            func.sum(Record.amount).label("total"),
        )
        .where(Record.book_id == book_id)
        .group_by("year", Record.type)
        .order_by("year")
    )
    rows = result.all()

    yearly: dict[str, dict] = {}
    for row in rows:
        y = row.year
        if y not in yearly:
            yearly[y] = {"year": y, "income": 0.0, "expense": 0.0}
        if row.type == "收入":
            yearly[y]["income"] = float(row.total or 0)
        elif row.type == "支出":
            yearly[y]["expense"] = float(row.total or 0)

    return list(yearly.values())


# ─── Accounts CRUD ───────────────────────────────────────────────────


@router.get("/accounts")
async def list_accounts(
    book_id: int,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)
    result = await session.execute(
        select(Account).where(Account.book_id == book_id).order_by(Account.type)
    )
    accounts = result.scalars().all()
    return [
        {
            "id": a.id,
            "name": a.name,
            "type": a.type,
            "balance": float(a.balance),
        }
        for a in accounts
    ]


@router.post("/accounts")
async def create_account(
    book_id: int,
    data: AccountCreate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)
    acc = Account(
        book_id=book_id,
        name=data.name,
        type=data.type,
        balance=data.balance,
    )
    session.add(acc)
    await session.commit()
    await session.refresh(acc)
    return {
        "id": acc.id,
        "name": acc.name,
        "type": acc.type,
        "balance": float(acc.balance),
    }


@router.put("/accounts/{account_id}")
async def update_account(
    account_id: int,
    data: AccountUpdate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    acc = await session.get(Account, account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="账户不存在")
    # Verify book ownership
    await _get_book(acc.book_id, user, session)

    if data.name is not None:
        acc.name = data.name
    if data.type is not None:
        acc.type = data.type
    if data.balance is not None:
        acc.balance = data.balance
    await session.commit()
    return {
        "id": acc.id,
        "name": acc.name,
        "type": acc.type,
        "balance": float(acc.balance),
    }


# ─── Categories ──────────────────────────────────────────────────────


@router.get("/categories")
async def list_categories(
    book_id: int,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)
    result = await session.execute(
        select(Category)
        .where(Category.book_id == book_id)
        .order_by(Category.type, Category.name)
    )
    cats = result.scalars().all()
    return [
        {"id": c.id, "name": c.name, "type": c.type, "parent_id": c.parent_id}
        for c in cats
    ]


# ─── Stats Overview ──────────────────────────────────────────────────


@router.get("/stats/overview")
async def stats_overview(
    book_id: int,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """统计概览：记账天数/交易笔数/净资产"""
    await _get_book(book_id, user, session)

    # 交易笔数
    tx_result = await session.execute(
        select(func.count(Record.id)).where(Record.book_id == book_id)
    )
    transactions = tx_result.scalar() or 0

    # 记账天数
    days_result = await session.execute(
        select(func.count(distinct(func.date(Record.record_time)))).where(
            Record.book_id == book_id
        )
    )
    days = days_result.scalar() or 0

    # 净资产 = 所有账户余额之和
    assets_result = await session.execute(
        select(func.sum(Account.balance)).where(Account.book_id == book_id)
    )
    net_assets = float(assets_result.scalar() or 0)

    return {"days": days, "transactions": transactions, "net_assets": net_assets}


# ─── CSV Import ──────────────────────────────────────────────────────


@router.post("/import/csv")
async def import_csv(
    book_id: int,
    file: UploadFile = File(...),
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    import logging

    logger = logging.getLogger(__name__)
    await _get_book(book_id, user, session)

    contents = await file.read()

    # 尝试多种编码，兼容 Excel 导出的各种格式
    decoded = None
    for encoding in ("utf-8-sig", "utf-16", "gbk", "gb18030", "utf-8", "latin-1"):
        try:
            decoded = contents.decode(encoding)
            break
        except UnicodeDecodeError, LookupError:
            continue
    if decoded is None:
        raise HTTPException(
            status_code=400, detail="无法识别文件编码，请使用 UTF-8 格式"
        )

    # 自动检测分隔符（CSV 可能是 tab/comma/semicolon）
    first_line = decoded.split("\n")[0] if decoded else ""
    if "\t" in first_line:
        delimiter = "\t"
    elif ";" in first_line and "," not in first_line:
        delimiter = ";"
    else:
        delimiter = ","

    logger.info(
        f"CSV import: detected delimiter={repr(delimiter)}, first_line={first_line[:200]}"
    )

    csv_reader = csv.DictReader(io.StringIO(decoded), delimiter=delimiter)

    # 标准化列头：去除空格和不可见字符
    if csv_reader.fieldnames:
        clean_names = []
        for name in csv_reader.fieldnames:
            # 去掉 BOM、空格、零宽字符
            cleaned = (
                name.strip()
                .replace("\ufeff", "")
                .replace("\u200b", "")
                .replace("\xa0", "")
            )
            clean_names.append(cleaned)
        csv_reader.fieldnames = clean_names
        logger.info(f"CSV import: cleaned fieldnames = {clean_names}")

    accounts_cache: dict[str, Account] = {}
    categories_cache: dict[str, Category] = {}

    records = []
    skipped = 0
    for row_idx, row in enumerate(csv_reader):
        # 标准化 row keys
        normalized_row: dict[str, str] = {}
        for k, v in row.items():
            if k is not None:
                clean_key = (
                    k.strip()
                    .replace("\ufeff", "")
                    .replace("\u200b", "")
                    .replace("\xa0", "")
                )
                normalized_row[clean_key] = (v or "").strip()

        if row_idx == 0:
            logger.info(f"CSV import: first row keys = {list(normalized_row.keys())}")
            logger.info(
                f"CSV import: first row vals = {list(normalized_row.values())[:8]}"
            )

        rtype = normalized_row.get("类型", "支出")

        # 金额解析：去除 ¥ 和逗号
        amount_raw = normalized_row.get("金额", "0")
        amount_str = (
            amount_raw.replace("¥", "").replace(",", "").replace("，", "").strip()
        )
        try:
            amount = abs(float(amount_str))
        except ValueError:
            logger.warning(f"CSV row {row_idx}: bad amount={amount_raw!r}")
            skipped += 1
            continue

        if amount == 0:
            skipped += 1
            continue

        # 日期解析
        date_str = normalized_row.get("日期", "")
        record_time = datetime.utcnow()
        if date_str:
            for fmt in (
                "%Y/%m/%d %H:%M",
                "%Y/%m/%d %H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y-%m-%d",
                "%Y/%m/%d",
            ):
                try:
                    record_time = datetime.strptime(date_str.strip(), fmt)
                    break
                except ValueError:
                    continue

        remark = normalized_row.get("备注", "")
        payee = normalized_row.get("商家", "")
        cat_name = normalized_row.get("分类", "") or "未分类"

        # 账户字段：优先用"付款"/"收款"，其次用"账户"
        from_acc_name = normalized_row.get("付款", "") or normalized_row.get("账户", "")
        to_acc_name = normalized_row.get("收款", "")

        cat = await _get_or_create_category(
            session, book_id, cat_name, rtype, categories_cache
        )
        from_acc = await _get_or_create_account(
            session, book_id, from_acc_name, cache=accounts_cache
        )
        to_acc = await _get_or_create_account(
            session, book_id, to_acc_name, cache=accounts_cache
        )

        rec = Record(
            book_id=book_id,
            type=rtype,
            amount=amount,
            account_id=from_acc.id if from_acc else None,
            target_account_id=to_acc.id if to_acc else None,
            category_id=cat.id if cat else None,
            record_time=record_time,
            payee=payee[:100] if payee else "",
            remark=remark[:500] if remark else "",
            creator_id=user.id,
        )
        records.append(rec)

    session.add_all(records)
    await session.commit()

    logger.info(f"CSV import: imported {len(records)} records, skipped {skipped}")
    return {
        "message": f"成功导入 {len(records)} 条记录"
        + (f"，跳过 {skipped} 条" if skipped else "")
    }
