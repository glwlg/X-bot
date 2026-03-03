from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    UploadFile,
    File,
    Query,
    Response,
    Form,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, distinct, or_, and_, case, literal, update, delete
from pydantic import BaseModel
from typing import Optional
import csv
import io
import json
import os
import asyncio
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone, timedelta

from api.core.database import get_async_session
from api.auth.users import current_active_user
from api.auth.models import User
from api.models.accounting import (
    Book,
    Account,
    Category,
    Record,
    Budget,
    ScheduledTask,
    DebtOrReimbursement,
    StatsPanel,
    OperationLog,
)
from shared.queue.dispatch_queue import dispatch_queue

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


class RecordUpdate(BaseModel):
    type: Optional[str] = None
    amount: Optional[float] = None
    category_name: Optional[str] = None
    account_name: Optional[str] = None
    target_account_name: Optional[str] = None
    payee: Optional[str] = None
    remark: Optional[str] = None
    record_time: Optional[str] = None


class AccountCreate(BaseModel):
    name: str
    type: str = "现金"
    balance: float = 0
    include_in_assets: bool = True


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    balance: Optional[float] = None
    include_in_assets: Optional[bool] = None


class BalanceAdjust(BaseModel):
    target_balance: float
    method: str = (
        "差额补记收支"  # 差额补记收支 / 差额补记转账 / 更改当前余额 / 设置初始余额
    )


class BudgetUpdate(BaseModel):
    month: str
    total_amount: float
    category_id: Optional[int] = None


class BookUpdate(BaseModel):
    name: str


class CategoryCreate(BaseModel):
    name: str
    type: str
    parent_id: Optional[int] = None


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    parent_id: Optional[int] = None


class ScheduledTaskCreate(BaseModel):
    name: str
    frequency: str
    next_run: str
    type: str
    amount: float
    account_name: str = ""
    target_account_name: str = ""
    category_name: str = "未分类"
    payee: str = ""
    remark: str = ""


class DebtCreate(BaseModel):
    type: str  # 借入 / 借出 / 报销
    contact: str
    amount: float
    due_date: Optional[str] = None
    remark: str = ""


class DebtRepay(BaseModel):
    amount: float
    account_name: str = ""
    # Usually repay logic involves recording an actual transaction plus reducing the debt balance
    # Can also capture the record `remark` or simply attach to debt
    remark: str = ""


class StatsPanelPayload(BaseModel):
    id: str
    name: str
    description: str = ""
    kind: str = "generic"
    enabled: bool = True
    is_custom: bool = True
    metric: str = "sum"
    subject: str = "dynamic"
    filters: list[str] = []
    default_type: str = "支出"
    default_range: str = "last_12_months"
    default_category: str = "全部分类"
    sort_order: int = 0


class StatsPanelsUpdate(BaseModel):
    panels: list[StatsPanelPayload]


class OperationLogPayload(BaseModel):
    id: str
    created_at: str = ""
    action: str
    detail: str = ""
    rollback: Optional[dict] = None
    rolled_back: bool = False
    rolled_back_at: Optional[str] = None


class OperationLogRollbackMark(BaseModel):
    rolled_back_at: Optional[str] = None


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


async def _serialize_record(session: AsyncSession, record: Record) -> dict:
    category_name = ""
    if record.category_id:
        category = await session.get(Category, record.category_id)
        if category:
            category_name = category.name

    account_name = ""
    if record.account_id:
        account = await session.get(Account, record.account_id)
        if account:
            account_name = account.name

    target_account_name = ""
    if record.target_account_id:
        target_account = await session.get(Account, record.target_account_id)
        if target_account:
            target_account_name = target_account.name

    return {
        "id": record.id,
        "type": record.type,
        "amount": float(record.amount),
        "category": category_name,
        "account": account_name,
        "target_account": target_account_name,
        "payee": record.payee or "",
        "remark": record.remark or "",
        "record_time": record.record_time.isoformat() if record.record_time else "",
    }


def _web_accounting_upload_root() -> Path:
    data_dir = str(os.getenv("DATA_DIR", "/app/data")).strip() or "/app/data"
    root = Path(data_dir).expanduser().resolve() / "system" / "web_accounting_uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _store_web_accounting_image(image_bytes: bytes, mime_type: str) -> str:
    root = _web_accounting_upload_root()
    ext = "jpg"
    if "/" in mime_type:
        maybe = str(mime_type.split("/", 1)[1] or "").strip().lower()
        if maybe and maybe.isascii() and maybe.isalnum() and len(maybe) <= 8:
            ext = maybe
    path = root / f"{uuid4().hex}.{ext}"
    path.write_bytes(image_bytes)
    return str(path)


async def _wait_for_dispatch_result(task_id: str, timeout_sec: float) -> dict:
    interval = 0.5
    deadline = asyncio.get_running_loop().time() + max(5.0, timeout_sec)
    while asyncio.get_running_loop().time() < deadline:
        result_obj = await dispatch_queue.latest_result(task_id)
        if result_obj is not None:
            payload = result_obj.to_dict()
            raw = payload.get("payload")
            payload_obj = dict(raw) if isinstance(raw, dict) else {}
            record_id_raw = payload_obj.get("record_id") or payload_obj.get(
                "accounting_record_id"
            )
            book_id_raw = payload_obj.get("book_id")
            try:
                record_id = int(record_id_raw)
            except (TypeError, ValueError):
                record_id = 0
            try:
                book_id = int(book_id_raw)
            except (TypeError, ValueError):
                book_id = 0

            message = str(
                payload_obj.get("message")
                or payload_obj.get("text")
                or payload.get("summary")
                or payload.get("error")
                or ""
            ).strip()
            return {
                "ok": bool(payload.get("ok")) and record_id > 0,
                "message": message[:500],
                "record_id": record_id,
                "book_id": book_id,
            }

        task_obj = await dispatch_queue.get_task(task_id)
        if task_obj is None:
            return {
                "ok": False,
                "message": "记账任务已丢失，请重试。",
                "record_id": 0,
                "book_id": 0,
            }
        if task_obj.status in {"failed", "cancelled"}:
            return {
                "ok": False,
                "message": str(task_obj.error or "AI 未能完成记账").strip()[:500],
                "record_id": 0,
                "book_id": 0,
            }

        await asyncio.sleep(interval)

    return {
        "ok": False,
        "message": "AI 识别超时，请稍后重试。",
        "record_id": 0,
        "book_id": 0,
    }


async def _run_web_image_quick_accounting(
    *,
    user: User,
    book_id: int,
    image_bytes: bytes,
    mime_type: str,
    note: str,
) -> dict:
    image_path = _store_web_accounting_image(image_bytes, mime_type)
    timeout_sec = float(os.getenv("WEB_ACCOUNTING_AUTO_TIMEOUT_SEC", "110"))
    worker_id = str(os.getenv("WEB_ACCOUNTING_MANAGER_ID", "manager-main")).strip()
    if not worker_id:
        worker_id = "manager-main"

    instruction = (
        "请先识别这张交易图片，再调用 ext_quick_accounting 完成真实记账。"
        "优先提取：类型、金额、分类、账户、交易对象、时间。"
        "若字段不完整，请基于常识补全，但不要虚构明显不存在的信息。"
    )
    user_note = str(note or "").strip()
    if user_note:
        instruction += f"\n\n补充说明：{user_note}"

    try:
        queued = await dispatch_queue.submit_task(
            worker_id=worker_id,
            instruction=instruction,
            source="web_accounting_auto_image",
            metadata={
                "execution_mode": "web_accounting_auto_image",
                "accounting_user_id": int(user.id),
                "accounting_book_id": int(book_id),
                "accounting_source": "web_clipboard",
                "web_accounting_image_path": image_path,
                "web_accounting_image_mime": mime_type,
            },
        )
        result = await _wait_for_dispatch_result(queued.task_id, timeout_sec)
        if result.get("book_id", 0) <= 0:
            result["book_id"] = int(book_id)
        return result
    finally:
        try:
            Path(image_path).unlink(missing_ok=True)
        except Exception:
            pass


def _parse_panel_filters(raw: str) -> list[str]:
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []

    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload if isinstance(item, str)]


def _serialize_stats_panel(panel: StatsPanel) -> dict:
    return {
        "id": panel.panel_id,
        "name": panel.name,
        "description": panel.description,
        "kind": panel.kind,
        "enabled": bool(panel.enabled),
        "is_custom": bool(panel.is_custom),
        "metric": panel.metric,
        "subject": panel.subject,
        "filters": _parse_panel_filters(panel.filters_json),
        "default_type": panel.default_type,
        "default_range": panel.default_range,
        "default_category": panel.default_category,
        "sort_order": int(panel.sort_order or 0),
    }


def _normalize_stats_panel_payload(payload: StatsPanelPayload, index: int) -> dict:
    filters = [str(item) for item in payload.filters if isinstance(item, str)]
    return {
        "panel_id": payload.id.strip()[:64],
        "name": payload.name.strip()[:100],
        "description": payload.description.strip()[:500],
        "kind": payload.kind.strip()[:20] or "generic",
        "enabled": bool(payload.enabled),
        "is_custom": bool(payload.is_custom),
        "metric": payload.metric.strip()[:20] or "sum",
        "subject": payload.subject.strip()[:20] or "dynamic",
        "filters_json": json.dumps(filters, ensure_ascii=False),
        "default_type": payload.default_type.strip()[:20] or "支出",
        "default_range": payload.default_range.strip()[:40] or "last_12_months",
        "default_category": payload.default_category.strip()[:100] or "全部分类",
        "sort_order": int(
            payload.sort_order if payload.sort_order is not None else index
        ),
    }


def _parse_iso_datetime(value: Optional[str], fallback: datetime) -> datetime:
    if not value:
        return fallback
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return fallback

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _serialize_operation_log(log: OperationLog) -> dict:
    rollback = None
    if log.rollback_json:
        try:
            payload = json.loads(log.rollback_json)
            if isinstance(payload, dict):
                rollback = payload
        except json.JSONDecodeError:
            rollback = None

    return {
        "id": log.log_id,
        "created_at": log.created_at.isoformat() if log.created_at else "",
        "action": log.action,
        "detail": log.detail,
        "rollback": rollback,
        "rolled_back": bool(log.rolled_back),
        "rolled_back_at": (
            log.rolled_back_at.isoformat() if log.rolled_back_at else None
        ),
    }


def _normalize_operation_log_payload(payload: OperationLogPayload) -> dict:
    rollback_json = ""
    if isinstance(payload.rollback, dict):
        rollback_json = json.dumps(payload.rollback, ensure_ascii=False)

    now = datetime.utcnow()
    return {
        "log_id": payload.id.strip()[:64],
        "created_at": _parse_iso_datetime(payload.created_at, now),
        "action": payload.action.strip()[:100],
        "detail": payload.detail.strip()[:500],
        "rollback_json": rollback_json,
        "rolled_back": bool(payload.rolled_back),
        "rolled_back_at": _parse_iso_datetime(payload.rolled_back_at, now)
        if payload.rolled_back
        else None,
        "updated_at": now,
    }


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


@router.put("/books/{book_id}")
async def update_book(
    book_id: int,
    data: BookUpdate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    book = await _get_book(book_id, user, session)
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="账本名称不能为空")

    book.name = name[:100]
    await session.commit()
    return {"id": book.id, "name": book.name}


@router.delete("/books/{book_id}")
async def delete_book(
    book_id: int,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    book = await _get_book(book_id, user, session)
    await session.delete(book)
    await session.commit()
    return {"message": "账本已删除"}


@router.get("/stats-panels")
async def list_stats_panels(
    book_id: int,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)

    result = await session.execute(
        select(StatsPanel)
        .where(
            StatsPanel.book_id == book_id,
            StatsPanel.owner_id == user.id,
        )
        .order_by(StatsPanel.sort_order.asc(), StatsPanel.id.asc())
    )
    panels = result.scalars().all()
    return [_serialize_stats_panel(panel) for panel in panels]


@router.put("/stats-panels")
async def replace_stats_panels(
    book_id: int,
    data: StatsPanelsUpdate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)

    await session.execute(
        delete(StatsPanel).where(
            StatsPanel.book_id == book_id,
            StatsPanel.owner_id == user.id,
        )
    )

    for index, payload in enumerate(data.panels):
        normalized = _normalize_stats_panel_payload(payload, index)
        if not normalized["panel_id"]:
            continue

        panel = StatsPanel(
            panel_id=normalized["panel_id"],
            book_id=book_id,
            owner_id=user.id,
            name=normalized["name"],
            description=normalized["description"],
            kind=normalized["kind"],
            enabled=normalized["enabled"],
            is_custom=normalized["is_custom"],
            metric=normalized["metric"],
            subject=normalized["subject"],
            filters_json=normalized["filters_json"],
            default_type=normalized["default_type"],
            default_range=normalized["default_range"],
            default_category=normalized["default_category"],
            sort_order=normalized["sort_order"],
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(panel)

    await session.commit()

    result = await session.execute(
        select(StatsPanel)
        .where(
            StatsPanel.book_id == book_id,
            StatsPanel.owner_id == user.id,
        )
        .order_by(StatsPanel.sort_order.asc(), StatsPanel.id.asc())
    )
    panels = result.scalars().all()
    return [_serialize_stats_panel(panel) for panel in panels]


@router.get("/operation-logs")
async def list_operation_logs(
    book_id: int,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)

    result = await session.execute(
        select(OperationLog)
        .where(
            OperationLog.book_id == book_id,
            OperationLog.owner_id == user.id,
        )
        .order_by(OperationLog.created_at.desc(), OperationLog.id.desc())
    )
    logs = result.scalars().all()
    return [_serialize_operation_log(log) for log in logs]


@router.post("/operation-logs")
async def create_operation_log(
    book_id: int,
    data: OperationLogPayload,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)

    normalized = _normalize_operation_log_payload(data)
    if not normalized["log_id"]:
        raise HTTPException(status_code=422, detail="日志ID不能为空")
    if not normalized["action"]:
        raise HTTPException(status_code=422, detail="日志动作不能为空")

    result = await session.execute(
        select(OperationLog).where(
            OperationLog.book_id == book_id,
            OperationLog.owner_id == user.id,
            OperationLog.log_id == normalized["log_id"],
        )
    )
    existing = result.scalars().first()

    if existing:
        existing.action = normalized["action"]
        existing.detail = normalized["detail"]
        existing.rollback_json = normalized["rollback_json"]
        existing.rolled_back = normalized["rolled_back"]
        existing.rolled_back_at = normalized["rolled_back_at"]
        existing.updated_at = normalized["updated_at"]
        log = existing
    else:
        log = OperationLog(
            log_id=normalized["log_id"],
            book_id=book_id,
            owner_id=user.id,
            action=normalized["action"],
            detail=normalized["detail"],
            rollback_json=normalized["rollback_json"],
            rolled_back=normalized["rolled_back"],
            rolled_back_at=normalized["rolled_back_at"],
            created_at=normalized["created_at"],
            updated_at=normalized["updated_at"],
        )
        session.add(log)

    await session.commit()
    await session.refresh(log)
    return _serialize_operation_log(log)


@router.post("/operation-logs/{log_id}/rollback")
async def mark_operation_log_rollback(
    log_id: str,
    book_id: int,
    data: OperationLogRollbackMark,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)

    result = await session.execute(
        select(OperationLog).where(
            OperationLog.book_id == book_id,
            OperationLog.owner_id == user.id,
            OperationLog.log_id == log_id,
        )
    )
    log = result.scalars().first()
    if not log:
        raise HTTPException(status_code=404, detail="日志不存在")

    log.rolled_back = True
    log.rolled_back_at = _parse_iso_datetime(data.rolled_back_at, datetime.utcnow())
    log.updated_at = datetime.utcnow()

    await session.commit()
    await session.refresh(log)
    return _serialize_operation_log(log)


@router.delete("/operation-logs")
async def clear_operation_logs(
    book_id: int,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)

    await session.execute(
        delete(OperationLog).where(
            OperationLog.book_id == book_id,
            OperationLog.owner_id == user.id,
        )
    )
    await session.commit()
    return {"message": "操作日志已清空"}


# ─── Records CRUD ────────────────────────────────────────────────────


@router.get("/records")
async def get_records(
    book_id: int,
    limit: int = Query(default=50, le=200),
    keyword: str = None,
    start_date: str = None,
    end_date: str = None,
    type: str = None,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)

    query = select(Record).where(Record.book_id == book_id)

    if start_date:
        query = query.where(Record.record_time >= datetime.fromisoformat(start_date))
    if end_date:
        query = query.where(Record.record_time <= datetime.fromisoformat(end_date))
    if type:
        query = query.where(Record.type == type)
    if keyword:
        # Search in remark, payee or through category join
        # For simplicity, search remark and payee here
        query = query.where(
            or_(
                Record.remark.ilike(f"%{keyword}%"),
                Record.payee.ilike(f"%{keyword}%"),
            )
        )

    result = await session.execute(
        query.order_by(Record.record_time.desc()).limit(limit)
    )
    records = result.scalars().all()
    return [await _serialize_record(session, r) for r in records]


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


@router.post("/records/auto-from-image")
async def auto_create_record_from_image(
    book_id: int,
    image: UploadFile = File(...),
    note: str = Form(default=""),
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)

    mime_type = str(image.content_type or "").strip().lower()
    if not mime_type.startswith("image/"):
        raise HTTPException(status_code=422, detail="仅支持图片识别记账")

    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=422, detail="图片内容为空")

    max_bytes = 8 * 1024 * 1024
    if len(image_bytes) > max_bytes:
        raise HTTPException(status_code=413, detail="图片过大，请控制在 8MB 以内")

    result = await _run_web_image_quick_accounting(
        user=user,
        book_id=book_id,
        image_bytes=image_bytes,
        mime_type=mime_type,
        note=note,
    )
    if not result.get("ok"):
        detail = str(result.get("message") or "AI 未能完成记账")
        raise HTTPException(status_code=422, detail=detail)

    return {
        "ok": True,
        "message": "记账成功",
        "book_id": int(result.get("book_id") or book_id),
        "record_id": int(result.get("record_id") or 0),
    }


# ─── Statistics ──────────────────────────────────────────────────────


def _parse_time_window(start_date: str, end_date: str) -> tuple[datetime, datetime]:
    try:
        start = datetime.fromisoformat(start_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="start_date 格式错误")

    try:
        end = datetime.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="end_date 格式错误")

    if end <= start:
        raise HTTPException(status_code=400, detail="end_date 必须晚于 start_date")

    return start, end


def _period_key_for_datetime(dt: datetime, granularity: str) -> str:
    if granularity == "day":
        return dt.strftime("%Y-%m-%d")
    if granularity == "week":
        iso_year, iso_week, _ = dt.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    if granularity == "month":
        return dt.strftime("%Y-%m")
    if granularity == "quarter":
        quarter = ((dt.month - 1) // 3) + 1
        return f"{dt.year}-Q{quarter}"
    return dt.strftime("%Y")


def _next_period_start(dt: datetime, granularity: str) -> datetime:
    base = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if granularity == "day":
        return base + timedelta(days=1)
    if granularity == "week":
        days_until_next_monday = 8 - base.isoweekday()
        return base + timedelta(days=days_until_next_monday)
    if granularity == "month":
        if base.month == 12:
            return datetime(base.year + 1, 1, 1)
        return datetime(base.year, base.month + 1, 1)
    if granularity == "quarter":
        quarter = ((base.month - 1) // 3) + 1
        next_month = quarter * 3 + 1
        next_year = base.year
        if next_month > 12:
            next_month = 1
            next_year += 1
        return datetime(next_year, next_month, 1)
    return datetime(base.year + 1, 1, 1)


def _resolve_balance_scope_accounts(
    accounts: list[Account],
    scope: str,
    account_type: str,
    account_id: Optional[int],
) -> list[Account]:
    if scope == "account":
        if account_id is None:
            raise HTTPException(status_code=400, detail="account_id 不能为空")
        matched = [account for account in accounts if account.id == account_id]
        if not matched:
            raise HTTPException(status_code=404, detail="账户不存在")
        return matched

    if scope == "account_type":
        type_name = account_type.strip()
        if not type_name:
            raise HTTPException(status_code=400, detail="account_type 不能为空")
        matched = [account for account in accounts if account.type == type_name]
        if not matched:
            raise HTTPException(status_code=404, detail="账户类型不存在")
        return matched

    if scope in {"net", "assets", "liabilities"}:
        return [account for account in accounts if account.include_in_assets]

    raise HTTPException(status_code=400, detail="scope 不合法")


def _scope_balance_total(
    scope: str,
    balances: dict[int, float],
    account_ids: list[int],
) -> float:
    values = [balances.get(account_id, 0.0) for account_id in account_ids]
    if scope == "assets":
        return sum(value for value in values if value > 0)
    if scope == "liabilities":
        return sum(value for value in values if value < 0)
    return sum(values)


def _apply_record_balance_change(
    record: Record,
    balances: dict[int, float],
    account_id_set: set[int],
) -> None:
    amount = float(record.amount or 0)
    from_id = record.account_id
    to_id = record.target_account_id

    if from_id in account_id_set:
        if record.type == "收入":
            balances[from_id] = balances.get(from_id, 0.0) + amount
        elif record.type == "支出":
            balances[from_id] = balances.get(from_id, 0.0) - amount
        elif record.type == "转账":
            balances[from_id] = balances.get(from_id, 0.0) - amount

    if record.type == "转账" and to_id in account_id_set:
        balances[to_id] = balances.get(to_id, 0.0) + amount


def _record_scope_delta(record: Record, account_id_set: set[int]) -> float:
    amount = float(record.amount or 0)
    from_in_scope = (
        record.account_id in account_id_set if record.account_id is not None else False
    )
    to_in_scope = (
        record.target_account_id in account_id_set
        if record.target_account_id is not None
        else False
    )

    if record.type == "收入":
        return amount if from_in_scope else 0.0
    if record.type == "支出":
        return -amount if from_in_scope else 0.0
    if record.type == "转账":
        if from_in_scope and not to_in_scope:
            return -amount
        if to_in_scope and not from_in_scope:
            return amount
    return 0.0


@router.get("/balance-trend")
async def get_balance_trend(
    book_id: int,
    start_date: str,
    end_date: str,
    granularity: str = "month",
    scope: str = "net",
    account_type: str = "",
    account_id: Optional[int] = None,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)
    start, end = _parse_time_window(start_date, end_date)

    allowed_granularity = {"day", "week", "month", "quarter", "year"}
    if granularity not in allowed_granularity:
        raise HTTPException(status_code=400, detail="granularity 不合法")

    accounts_result = await session.execute(
        select(Account)
        .where(Account.book_id == book_id)
        .order_by(Account.type.asc(), Account.id.asc())
    )
    all_accounts = list(accounts_result.scalars().all())

    scoped_accounts = _resolve_balance_scope_accounts(
        all_accounts,
        scope,
        account_type,
        account_id,
    )
    scoped_ids = [account.id for account in scoped_accounts]
    scoped_id_set = set(scoped_ids)
    balances = {account.id: float(account.balance or 0) for account in scoped_accounts}

    records: list[Record] = []
    if scoped_ids:
        records_result = await session.execute(
            select(Record)
            .where(
                Record.book_id == book_id,
                Record.record_time < end,
                or_(
                    Record.account_id.in_(scoped_ids),
                    Record.target_account_id.in_(scoped_ids),
                ),
            )
            .order_by(Record.record_time.asc(), Record.id.asc())
        )
        records = list(records_result.scalars().all())

    index = 0
    while index < len(records) and records[index].record_time < start:
        _apply_record_balance_change(records[index], balances, scoped_id_set)
        index += 1

    previous_balance = _scope_balance_total(scope, balances, scoped_ids)
    rows = []
    cursor = start

    while cursor < end:
        next_cursor = _next_period_start(cursor, granularity)
        if next_cursor <= cursor:
            next_cursor = cursor + timedelta(days=1)
        if next_cursor > end:
            next_cursor = end

        period_income = 0.0
        period_expense = 0.0
        while index < len(records) and records[index].record_time < next_cursor:
            record = records[index]
            delta = _record_scope_delta(record, scoped_id_set)
            if delta > 0:
                period_income += delta
            elif delta < 0:
                period_expense += -delta

            _apply_record_balance_change(record, balances, scoped_id_set)
            index += 1

        current_balance = _scope_balance_total(scope, balances, scoped_ids)
        rows.append(
            {
                "period": _period_key_for_datetime(cursor, granularity),
                "period_start": cursor.isoformat(),
                "period_end": next_cursor.isoformat(),
                "balance": round(current_balance, 2),
                "change": round(current_balance - previous_balance, 2),
                "income": round(period_income, 2),
                "expense": round(period_expense, 2),
            }
        )

        previous_balance = current_balance
        cursor = next_cursor

    return rows


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

    if month == 0:
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1)
    else:
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

    if month == 0:
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1)
    else:
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

    if month == 0:
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1)
    else:
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


@router.get("/records/category-summary-range")
async def category_summary_range(
    book_id: int,
    start_date: str,
    end_date: str,
    type: str = "支出",
    category: str = "",
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """按任意日期范围做分类汇总"""
    await _get_book(book_id, user, session)
    start, end = _parse_time_window(start_date, end_date)

    query = (
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

    category_name = category.strip()
    if category_name and category_name not in {"全部", "全部分类"}:
        if category_name == "未分类":
            query = query.where(Record.category_id.is_(None))
        else:
            query = query.where(Category.name == category_name)

    result = await session.execute(query)
    rows = result.all()
    return [
        {"category": r.category or "未分类", "amount": float(r.total or 0)}
        for r in rows
    ]


@router.get("/records/range-summary")
async def range_summary(
    book_id: int,
    start_date: str,
    end_date: str,
    granularity: str = "month",
    category: str = "",
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """按任意日期范围和粒度做收支趋势汇总"""
    await _get_book(book_id, user, session)
    start, end = _parse_time_window(start_date, end_date)

    allowed = {"day", "week", "month", "quarter", "year"}
    if granularity not in allowed:
        raise HTTPException(status_code=400, detail="granularity 不合法")

    query = (
        select(
            func.strftime("%Y-%m-%d", Record.record_time).label("day"),
            Record.type,
            func.sum(Record.amount).label("total"),
            func.count(Record.id).label("record_count"),
        )
        .join(Category, Record.category_id == Category.id, isouter=True)
        .where(
            Record.book_id == book_id,
            Record.record_time >= start,
            Record.record_time < end,
        )
        .group_by("day", Record.type)
        .order_by("day")
    )

    category_name = category.strip()
    if category_name and category_name not in {"全部", "全部分类"}:
        if category_name == "未分类":
            query = query.where(Record.category_id.is_(None))
        else:
            query = query.where(Category.name == category_name)

    result = await session.execute(query)
    rows = result.all()

    grouped: dict[str, dict[str, float | int | str]] = {}
    for row in rows:
        day = row.day
        if not day:
            continue
        try:
            dt = datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            continue

        key = _period_key_for_datetime(dt, granularity)
        if key not in grouped:
            grouped[key] = {
                "period": key,
                "income": 0.0,
                "expense": 0.0,
                "income_count": 0,
                "expense_count": 0,
            }

        if row.type == "收入":
            grouped[key]["income"] = float(grouped[key]["income"]) + float(
                row.total or 0
            )
            grouped[key]["income_count"] = int(grouped[key]["income_count"]) + int(
                row.record_count or 0
            )
        elif row.type == "支出":
            grouped[key]["expense"] = float(grouped[key]["expense"]) + float(
                row.total or 0
            )
            grouped[key]["expense_count"] = int(grouped[key]["expense_count"]) + int(
                row.record_count or 0
            )

    return [grouped[k] for k in sorted(grouped.keys())]


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


@router.get("/records/{record_id}")
async def get_record_detail(
    record_id: int,
    book_id: int,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)

    record = await session.get(Record, record_id)
    if not record or record.book_id != book_id:
        raise HTTPException(status_code=404, detail="记录不存在")

    return await _serialize_record(session, record)


@router.put("/records/{record_id}")
async def update_record(
    record_id: int,
    book_id: int,
    data: RecordUpdate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)

    record = await session.get(Record, record_id)
    if not record or record.book_id != book_id:
        raise HTTPException(status_code=404, detail="记录不存在")

    if data.amount is not None:
        if data.amount <= 0:
            raise HTTPException(status_code=400, detail="金额必须大于0")
        record.amount = data.amount

    if data.type is not None:
        new_type = data.type.strip()
        if not new_type:
            raise HTTPException(status_code=400, detail="类型不能为空")
        if new_type not in {"支出", "收入", "转账"}:
            raise HTTPException(status_code=400, detail="类型不合法")
        record.type = new_type

    current_category_name = ""
    if record.category_id:
        current_category = await session.get(Category, record.category_id)
        if current_category:
            current_category_name = current_category.name

    should_update_category = False
    category_name_to_use: Optional[str] = None
    if data.category_name is not None:
        category_name_to_use = data.category_name.strip()
        should_update_category = True
    elif data.type is not None:
        category_name_to_use = current_category_name
        should_update_category = True

    if should_update_category:
        if category_name_to_use:
            category = await _get_or_create_category(
                session,
                book_id,
                category_name_to_use,
                record.type,
            )
            record.category_id = category.id if category else None
        else:
            record.category_id = None

    if data.account_name is not None:
        account_name = data.account_name.strip()
        if account_name:
            account = await _get_or_create_account(session, book_id, account_name)
            record.account_id = account.id if account else None
        else:
            record.account_id = None

    if data.target_account_name is not None:
        target_account_name = data.target_account_name.strip()
        if target_account_name:
            target_account = await _get_or_create_account(
                session,
                book_id,
                target_account_name,
            )
            record.target_account_id = target_account.id if target_account else None
        else:
            record.target_account_id = None
    elif data.type is not None and record.type != "转账":
        record.target_account_id = None

    if data.record_time is not None:
        raw_record_time = data.record_time.strip()
        if not raw_record_time:
            raise HTTPException(status_code=400, detail="记录时间不能为空")
        try:
            record.record_time = datetime.fromisoformat(raw_record_time)
        except ValueError:
            raise HTTPException(status_code=400, detail="记录时间格式错误")

    if data.payee is not None:
        record.payee = data.payee[:100]

    if data.remark is not None:
        record.remark = data.remark[:500]

    await session.commit()
    await session.refresh(record)

    return {
        "message": "记录已更新",
        "record": await _serialize_record(session, record),
    }


@router.delete("/records/{record_id}")
async def delete_record(
    record_id: int,
    book_id: int,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)

    record = await session.get(Record, record_id)
    if not record or record.book_id != book_id:
        raise HTTPException(status_code=404, detail="记录不存在")

    await session.delete(record)
    await session.commit()
    return {"message": "记录已删除"}


# ─── Helper: calculate dynamic account balance ──────────────────────


async def _calc_account_balance(
    session: AsyncSession,
    account_id: int,
    initial_balance: float,
) -> float:
    """当前余额 = 初始余额 + 该账户的收入 - 该账户的支出 + 转入 - 转出"""
    # 作为付款账户（account_id）：支出减钱，收入加钱
    # 作为收款账户（target_account_id）：转账加钱
    result = await session.execute(
        select(
            func.coalesce(
                func.sum(
                    case(
                        # 收入记录到该账户 -> +
                        (
                            and_(
                                Record.account_id == account_id, Record.type == "收入"
                            ),
                            Record.amount,
                        ),
                        # 支出记录从该账户 -> -
                        (
                            and_(
                                Record.account_id == account_id, Record.type == "支出"
                            ),
                            -Record.amount,
                        ),
                        # 转账：从该账户转出 -> -
                        (
                            and_(
                                Record.account_id == account_id, Record.type == "转账"
                            ),
                            -Record.amount,
                        ),
                        # 转账：转入该账户 -> +
                        (
                            and_(
                                Record.target_account_id == account_id,
                                Record.type == "转账",
                            ),
                            Record.amount,
                        ),
                        else_=literal(0),
                    )
                ),
                literal(0),
            )
        ).where(
            or_(
                Record.account_id == account_id,
                Record.target_account_id == account_id,
            )
        )
    )
    tx_sum = float(result.scalar() or 0)
    return initial_balance + tx_sum


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
    enriched = []
    for a in accounts:
        current_balance = await _calc_account_balance(session, a.id, float(a.balance))
        enriched.append(
            {
                "id": a.id,
                "name": a.name,
                "type": a.type,
                "initial_balance": float(a.balance),
                "balance": current_balance,
                "include_in_assets": a.include_in_assets,
            }
        )
    return enriched


@router.get("/accounts/{account_id}")
async def get_account_detail(
    account_id: int,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """账户详情：包含动态余额和基本信息"""
    acc = await session.get(Account, account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="账户不存在")
    await _get_book(acc.book_id, user, session)

    current_balance = await _calc_account_balance(session, acc.id, float(acc.balance))
    return {
        "id": acc.id,
        "name": acc.name,
        "type": acc.type,
        "initial_balance": float(acc.balance),
        "balance": current_balance,
        "include_in_assets": acc.include_in_assets,
        "book_id": acc.book_id,
    }


@router.get("/accounts/{account_id}/records")
async def get_account_records(
    account_id: int,
    limit: int = Query(default=50, le=200),
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """某一账户的交易记录"""
    acc = await session.get(Account, account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="账户不存在")
    await _get_book(acc.book_id, user, session)

    result = await session.execute(
        select(Record)
        .where(
            or_(
                Record.account_id == account_id,
                Record.target_account_id == account_id,
            )
        )
        .order_by(Record.record_time.desc())
        .limit(limit)
    )
    records = result.scalars().all()

    return [await _serialize_record(session, r) for r in records]


@router.get("/accounts/{account_id}/balance-trend")
async def get_account_balance_trend(
    account_id: int,
    days: int = Query(default=30, le=365),
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """账户余额趋势（按日聚合 - 用于趋势图）"""
    acc = await session.get(Account, account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="账户不存在")
    await _get_book(acc.book_id, user, session)

    from datetime import timedelta

    start = datetime.utcnow() - timedelta(days=days)

    result = await session.execute(
        select(
            func.strftime("%Y-%m-%d", Record.record_time).label("day"),
            func.sum(
                case(
                    (
                        and_(Record.account_id == account_id, Record.type == "收入"),
                        Record.amount,
                    ),
                    (
                        and_(Record.account_id == account_id, Record.type == "支出"),
                        -Record.amount,
                    ),
                    (
                        and_(Record.account_id == account_id, Record.type == "转账"),
                        -Record.amount,
                    ),
                    (
                        and_(
                            Record.target_account_id == account_id,
                            Record.type == "转账",
                        ),
                        Record.amount,
                    ),
                    else_=literal(0),
                )
            ).label("daily_change"),
        )
        .where(
            or_(
                Record.account_id == account_id,
                Record.target_account_id == account_id,
            ),
            Record.record_time >= start,
        )
        .group_by("day")
        .order_by("day")
    )
    rows = result.all()

    # 计算累积余额
    initial = float(acc.balance)
    # 先算 start 之前的所有交易累加
    pre_result = await session.execute(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                Record.account_id == account_id, Record.type == "收入"
                            ),
                            Record.amount,
                        ),
                        (
                            and_(
                                Record.account_id == account_id, Record.type == "支出"
                            ),
                            -Record.amount,
                        ),
                        (
                            and_(
                                Record.account_id == account_id, Record.type == "转账"
                            ),
                            -Record.amount,
                        ),
                        (
                            and_(
                                Record.target_account_id == account_id,
                                Record.type == "转账",
                            ),
                            Record.amount,
                        ),
                        else_=literal(0),
                    )
                ),
                literal(0),
            )
        ).where(
            or_(
                Record.account_id == account_id,
                Record.target_account_id == account_id,
            ),
            Record.record_time < start,
        )
    )
    pre_sum = float(pre_result.scalar() or 0)
    running = initial + pre_sum

    trend = []
    for row in rows:
        running += float(row.daily_change or 0)
        trend.append({"date": row.day, "balance": round(running, 2)})

    return trend


@router.post("/accounts/{account_id}/adjust-balance")
async def adjust_balance(
    account_id: int,
    data: BalanceAdjust,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """余额校正"""
    acc = await session.get(Account, account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="账户不存在")
    await _get_book(acc.book_id, user, session)

    current = await _calc_account_balance(session, acc.id, float(acc.balance))
    diff = data.target_balance - current

    if data.method == "更改当前余额" or data.method == "设置初始余额":
        # 直接改初始余额使得当前余额 = target
        acc.balance = float(acc.balance) + diff
        await session.commit()
    elif data.method == "差额补记转账":
        if abs(diff) < 0.01:
            return {"message": "余额无需调整"}
        rec = Record(
            book_id=acc.book_id,
            type="转账",
            amount=abs(diff),
            account_id=acc.id if diff < 0 else None,
            target_account_id=acc.id if diff > 0 else None,
            category_id=None,
            record_time=datetime.utcnow(),
            payee="",
            remark=f"余额校正(无账户转账): {current:.2f} → {data.target_balance:.2f}",
            creator_id=user.id,
        )
        session.add(rec)
        await session.commit()
    else:
        # 差额补记收支：创建一条调整记录
        if abs(diff) < 0.01:
            return {"message": "余额无需调整"}
        rec = Record(
            book_id=acc.book_id,
            type="收入" if diff > 0 else "支出",
            amount=abs(diff),
            account_id=acc.id,
            category_id=None,
            record_time=datetime.utcnow(),
            payee="",
            remark=f"余额校正: {current:.2f} → {data.target_balance:.2f}",
            creator_id=user.id,
        )
        session.add(rec)
        await session.commit()

    new_balance = await _calc_account_balance(session, acc.id, float(acc.balance))
    return {"message": "余额已调整", "balance": new_balance}


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
        include_in_assets=data.include_in_assets,
    )
    session.add(acc)
    await session.commit()
    await session.refresh(acc)
    return {
        "id": acc.id,
        "name": acc.name,
        "type": acc.type,
        "initial_balance": float(acc.balance),
        "balance": float(acc.balance),
        "include_in_assets": acc.include_in_assets,
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
    await _get_book(acc.book_id, user, session)

    if data.name is not None:
        acc.name = data.name
    if data.type is not None:
        acc.type = data.type
    if data.balance is not None:
        acc.balance = data.balance
    if data.include_in_assets is not None:
        acc.include_in_assets = data.include_in_assets
    await session.commit()
    current = await _calc_account_balance(session, acc.id, float(acc.balance))
    return {
        "id": acc.id,
        "name": acc.name,
        "type": acc.type,
        "initial_balance": float(acc.balance),
        "balance": current,
        "include_in_assets": acc.include_in_assets,
    }


@router.delete("/accounts/{account_id}")
async def delete_account(
    account_id: int,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    acc = await session.get(Account, account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="账户不存在")
    await _get_book(acc.book_id, user, session)
    await session.delete(acc)
    await session.commit()
    return {"message": "账户已删除"}


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


@router.post("/categories")
async def create_category(
    book_id: int,
    data: CategoryCreate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)

    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="分类名称不能为空")

    category_type = data.type.strip()
    if category_type not in {"支出", "收入", "转账"}:
        raise HTTPException(status_code=400, detail="分类类型不合法")

    exists_result = await session.execute(
        select(Category).where(
            Category.book_id == book_id,
            Category.name == name,
            Category.type == category_type,
        )
    )
    exists = exists_result.scalars().first()
    if exists:
        return {
            "id": exists.id,
            "name": exists.name,
            "type": exists.type,
            "parent_id": exists.parent_id,
        }

    category = Category(
        book_id=book_id,
        name=name[:100],
        type=category_type,
        parent_id=data.parent_id,
    )
    session.add(category)
    await session.commit()
    await session.refresh(category)

    return {
        "id": category.id,
        "name": category.name,
        "type": category.type,
        "parent_id": category.parent_id,
    }


@router.put("/categories/{category_id}")
async def update_category(
    category_id: int,
    book_id: int,
    data: CategoryUpdate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)

    category = await session.get(Category, category_id)
    if not category or category.book_id != book_id:
        raise HTTPException(status_code=404, detail="分类不存在")

    if data.name is not None:
        next_name = data.name.strip()
        if not next_name:
            raise HTTPException(status_code=400, detail="分类名称不能为空")
        category.name = next_name[:100]

    if data.type is not None:
        next_type = data.type.strip()
        if next_type not in {"支出", "收入", "转账"}:
            raise HTTPException(status_code=400, detail="分类类型不合法")
        category.type = next_type

    if data.parent_id is not None:
        category.parent_id = data.parent_id

    await session.commit()
    await session.refresh(category)
    return {
        "id": category.id,
        "name": category.name,
        "type": category.type,
        "parent_id": category.parent_id,
    }


@router.delete("/categories/{category_id}")
async def delete_category(
    category_id: int,
    book_id: int,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)

    category = await session.get(Category, category_id)
    if not category or category.book_id != book_id:
        raise HTTPException(status_code=404, detail="分类不存在")

    await session.execute(
        update(Record).where(Record.category_id == category_id).values(category_id=None)
    )
    await session.execute(
        update(Budget).where(Budget.category_id == category_id).values(category_id=None)
    )
    await session.execute(
        update(ScheduledTask)
        .where(ScheduledTask.category_id == category_id)
        .values(category_id=None)
    )
    await session.delete(category)
    await session.commit()
    return {"message": "分类已删除"}


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

    # 净资产 = 所有(计入资产的)账户动态余额之和
    acc_result = await session.execute(
        select(Account).where(
            Account.book_id == book_id, Account.include_in_assets.is_(True)
        )
    )
    all_accounts = acc_result.scalars().all()
    net_assets = 0.0
    for a in all_accounts:
        net_assets += await _calc_account_balance(session, a.id, float(a.balance))

    return {"days": days, "transactions": transactions, "net_assets": net_assets}


# ─── CSV Import / Export ─────────────────────────────────────────────


@router.get("/export/csv")
async def export_csv(
    book_id: int,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)

    result = await session.execute(
        select(Record)
        .where(Record.book_id == book_id)
        .order_by(Record.record_time.desc(), Record.id.desc())
    )
    records = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "ID",
            "类型",
            "金额",
            "分类",
            "账户",
            "转入账户",
            "交易对象",
            "备注",
            "记录时间",
        ]
    )

    for record in records:
        payload = await _serialize_record(session, record)
        writer.writerow(
            [
                payload["id"],
                payload["type"],
                payload["amount"],
                payload["category"],
                payload["account"],
                payload["target_account"],
                payload["payee"],
                payload["remark"],
                payload["record_time"],
            ]
        )

    filename = f"accounting_{book_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    content = "\ufeff" + output.getvalue()
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
    return Response(
        content=content, media_type="text/csv; charset=utf-8", headers=headers
    )


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
        except (UnicodeDecodeError, LookupError):
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


# ─── Budgets CRUD ───────────────────────────────────────────────────


@router.get("/budgets")
async def get_budgets(
    book_id: int,
    month: str = None,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)
    query = select(Budget).where(Budget.book_id == book_id)
    if month:
        query = query.where(Budget.month == month)

    result = await session.execute(query.order_by(Budget.month.desc()))
    budgets = result.scalars().all()

    enriched = []
    for b in budgets:
        cat_name = ""
        if b.category_id:
            cat = await session.get(Category, b.category_id)
            if cat:
                cat_name = cat.name
        enriched.append(
            {
                "id": b.id,
                "month": b.month,
                "total_amount": float(b.total_amount),
                "category_id": b.category_id,
                "category_name": cat_name,
            }
        )
    return enriched


@router.post("/budgets")
async def create_or_update_budget(
    book_id: int,
    data: BudgetUpdate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)

    # Check if a budget already exists
    query = select(Budget).where(Budget.book_id == book_id, Budget.month == data.month)
    if data.category_id:
        query = query.where(Budget.category_id == data.category_id)
    else:
        query = query.where(Budget.category_id.is_(None))

    res = await session.execute(query)
    existing = res.scalars().first()

    if existing:
        existing.total_amount = data.total_amount
        budget = existing
    else:
        budget = Budget(
            book_id=book_id,
            month=data.month,
            total_amount=data.total_amount,
            category_id=data.category_id,
        )
        session.add(budget)

    await session.commit()
    return {"message": "预算保存成功"}


# ─── Scheduled Tasks CRUD ───────────────────────────────────────────


@router.get("/scheduled-tasks")
async def get_scheduled_tasks(
    book_id: int,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)
    result = await session.execute(
        select(ScheduledTask)
        .where(ScheduledTask.book_id == book_id)
        .order_by(ScheduledTask.next_run.asc())
    )
    tasks = result.scalars().all()

    enriched = []
    for t in tasks:
        # Resolve names for display
        cat_name = ""
        acc_name = ""
        target_acc_name = ""
        if t.category_id:
            cat = await session.get(Category, t.category_id)
            if cat:
                cat_name = cat.name
        if t.account_id:
            acc = await session.get(Account, t.account_id)
            if acc:
                acc_name = acc.name
        if t.target_account_id:
            tacc = await session.get(Account, t.target_account_id)
            if tacc:
                target_acc_name = tacc.name

        enriched.append(
            {
                "id": t.id,
                "name": t.name,
                "frequency": t.frequency,
                "next_run": t.next_run.isoformat(),
                "type": t.type,
                "amount": float(t.amount),
                "category_name": cat_name,
                "account_name": acc_name,
                "target_account_name": target_acc_name,
                "payee": t.payee or "",
                "remark": t.remark or "",
                "is_active": t.is_active,
            }
        )
    return enriched


@router.post("/scheduled-tasks")
async def create_scheduled_task(
    book_id: int,
    data: ScheduledTaskCreate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)

    # 1. Category
    cat_name = data.category_name.strip() or "未分类"
    res = await session.execute(
        select(Category).where(
            Category.book_id == book_id,
            Category.name == cat_name,
            Category.type == data.type,
        )
    )
    cat = res.scalars().first()
    if not cat:
        cat = Category(book_id=book_id, name=cat_name, type=data.type)
        session.add(cat)
        await session.flush()

    # 2. Accounts
    from_acc_id = None
    to_acc_id = None
    if data.account_name:
        res_acc1 = await session.execute(
            select(Account).where(
                Account.book_id == book_id, Account.name == data.account_name
            )
        )
        acc1 = res_acc1.scalars().first()
        if not acc1:
            acc1 = Account(book_id=book_id, name=data.account_name)
            session.add(acc1)
            await session.flush()
        from_acc_id = acc1.id

    if data.target_account_name:
        res_acc2 = await session.execute(
            select(Account).where(
                Account.book_id == book_id, Account.name == data.target_account_name
            )
        )
        acc2 = res_acc2.scalars().first()
        if not acc2:
            acc2 = Account(book_id=book_id, name=data.target_account_name)
            session.add(acc2)
            await session.flush()
        to_acc_id = acc2.id

    try:
        next_r = datetime.fromisoformat(data.next_run)
    except Exception:
        next_r = datetime.utcnow()

    task = ScheduledTask(
        book_id=book_id,
        name=data.name,
        frequency=data.frequency,
        next_run=next_r,
        type=data.type,
        amount=data.amount,
        account_id=from_acc_id,
        target_account_id=to_acc_id,
        category_id=cat.id,
        payee=data.payee,
        remark=data.remark,
        creator_id=user.id,
    )
    session.add(task)
    await session.commit()
    return {"id": task.id, "message": "周期任务创建成功"}


@router.delete("/scheduled-tasks/{task_id}")
async def delete_scheduled_task(
    book_id: int,
    task_id: int,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)
    task = await session.get(ScheduledTask, task_id)
    if not task or task.book_id != book_id:
        raise HTTPException(status_code=404, detail="任务不存在")
    await session.delete(task)
    await session.commit()
    return {"message": "删除成功"}


# ─── Debts & Reimbursements CRUD ────────────────────────────────────


@router.get("/debts")
async def get_debts(
    book_id: int,
    type: str = None,  # optional filter
    is_settled: bool = None,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)
    query = select(DebtOrReimbursement).where(DebtOrReimbursement.book_id == book_id)
    if type:
        query = query.where(DebtOrReimbursement.type == type)
    if is_settled is not None:
        query = query.where(DebtOrReimbursement.is_settled == is_settled)

    result = await session.execute(
        query.order_by(DebtOrReimbursement.created_at.desc())
    )
    debts = result.scalars().all()

    return [
        {
            "id": d.id,
            "type": d.type,
            "contact": d.contact,
            "total_amount": float(d.total_amount),
            "remaining_amount": float(d.remaining_amount),
            "due_date": d.due_date.isoformat() if d.due_date else None,
            "remark": d.remark,
            "is_settled": d.is_settled,
            "created_at": d.created_at.isoformat(),
        }
        for d in debts
    ]


@router.post("/debts")
async def create_debt(
    book_id: int,
    data: DebtCreate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)

    due_d = None
    if data.due_date:
        try:
            due_d = datetime.fromisoformat(data.due_date)
        except Exception:
            pass

    debt = DebtOrReimbursement(
        book_id=book_id,
        type=data.type,
        contact=data.contact[:100],
        total_amount=data.amount,
        remaining_amount=data.amount,
        due_date=due_d,
        remark=data.remark[:500],
        creator_id=user.id,
    )
    session.add(debt)
    await session.commit()
    return {"id": debt.id, "message": f"{data.type}记录创建成功"}


@router.post("/debts/{debt_id}/repay")
async def repay_debt(
    book_id: int,
    debt_id: int,
    data: DebtRepay,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_book(book_id, user, session)
    debt = await session.get(DebtOrReimbursement, debt_id)
    if not debt or debt.book_id != book_id:
        raise HTTPException(status_code=404, detail="记录不存在")
    if debt.is_settled:
        raise HTTPException(status_code=400, detail="已结清")

    if data.amount <= 0:
        raise HTTPException(status_code=400, detail="金额必须大于0")

    repay_amount = min(float(data.amount), float(debt.remaining_amount))
    debt.remaining_amount = float(debt.remaining_amount) - repay_amount

    if debt.remaining_amount <= 0.01:
        debt.remaining_amount = 0
        debt.is_settled = True

    # Ideally, we would also create a Record representing the monetary transaction here.
    # For simplicity, we just adjust the debt object state for now.

    await session.commit()
    return {
        "message": "还款成功",
        "remaining_amount": debt.remaining_amount,
        "is_settled": debt.is_settled,
    }
