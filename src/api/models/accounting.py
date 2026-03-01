from sqlalchemy import String, ForeignKey, Numeric, DateTime
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
