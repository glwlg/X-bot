from sqlalchemy import String, ForeignKey, Numeric, DateTime, Text, UniqueConstraint
from sqlalchemy.orm import mapped_column, Mapped
from datetime import datetime
from api.core.database import Base


class Book(Base):
    __tablename__ = "accounting_books"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )


class Account(Base):
    __tablename__ = "accounting_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_books.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[str] = mapped_column(String(50), default="现金", nullable=False)
    balance: Mapped[float] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    include_in_assets: Mapped[bool] = mapped_column(default=True, nullable=False)


class AccountAlias(Base):
    __tablename__ = "accounting_account_aliases"
    __table_args__ = (
        UniqueConstraint(
            "book_id",
            "name",
            name="uq_accounting_account_alias_book_name",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_books.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_accounts.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)


class Category(Base):
    __tablename__ = "accounting_categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_books.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)  # 支出/收入/转账
    parent_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_categories.id"), nullable=True
    )


class Record(Base):
    __tablename__ = "accounting_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_books.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_accounts.id"), nullable=True
    )
    target_account_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_accounts.id"), nullable=True
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_categories.id"), nullable=True
    )
    record_time: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    payee: Mapped[str] = mapped_column(String(100), nullable=True)
    remark: Mapped[str] = mapped_column(String(500), nullable=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)


class Budget(Base):
    __tablename__ = "accounting_budgets"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_books.id", ondelete="CASCADE"), nullable=False
    )
    month: Mapped[str] = mapped_column(String(7), nullable=False)  # Format: YYYY-MM
    total_amount: Mapped[float] = mapped_column(
        Numeric(12, 2), default=0, nullable=False
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_categories.id", ondelete="CASCADE"), nullable=True
    )


class ScheduledTask(Base):
    __tablename__ = "accounting_scheduled_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_books.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # 频率: 每天 / 每周 / 每月 / 每年
    frequency: Mapped[str] = mapped_column(String(20), nullable=False)
    next_run: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Payload for the auto-generated record
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_accounts.id"), nullable=True
    )
    target_account_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_accounts.id"), nullable=True
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_categories.id"), nullable=True
    )
    payee: Mapped[str] = mapped_column(String(100), nullable=True)
    remark: Mapped[str] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)


class DebtOrReimbursement(Base):
    __tablename__ = "accounting_debts"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_books.id", ondelete="CASCADE"), nullable=False
    )
    # 借入 / 借出 / 报销
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    # The actual person or entity involved
    contact: Mapped[str] = mapped_column(String(100), nullable=False)
    # Initial amount
    total_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    # Current remaining/unsettled amount
    remaining_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    # Optional due date
    due_date: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    remark: Mapped[str] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    is_settled: Mapped[bool] = mapped_column(default=False, nullable=False)
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)


class StatsPanel(Base):
    __tablename__ = "accounting_stats_panels"
    __table_args__ = (
        UniqueConstraint(
            "book_id",
            "owner_id",
            "panel_id",
            name="uq_accounting_stats_panel_owner_book_panel",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    panel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_books.id", ondelete="CASCADE"), nullable=False
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="generic")
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    is_custom: Mapped[bool] = mapped_column(nullable=False, default=True)
    metric: Mapped[str] = mapped_column(String(20), nullable=False, default="sum")
    subject: Mapped[str] = mapped_column(String(20), nullable=False, default="dynamic")
    filters_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    default_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="支出"
    )
    default_range: Mapped[str] = mapped_column(
        String(40), nullable=False, default="last_12_months"
    )
    default_category: Mapped[str] = mapped_column(
        String(100), nullable=False, default="全部分类"
    )
    sort_order: Mapped[int] = mapped_column(nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )


class OperationLog(Base):
    __tablename__ = "accounting_operation_logs"
    __table_args__ = (
        UniqueConstraint(
            "book_id",
            "owner_id",
            "log_id",
            name="uq_accounting_operation_log_owner_book_log",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    log_id: Mapped[str] = mapped_column(String(64), nullable=False)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_books.id", ondelete="CASCADE"), nullable=False
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    action: Mapped[str] = mapped_column(String(100), nullable=False)
    detail: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    rollback_json: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rolled_back: Mapped[bool] = mapped_column(nullable=False, default=False)
    rolled_back_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
