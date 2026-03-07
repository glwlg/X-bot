from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import mapped_column, Mapped
from api.core.database import Base


class PlatformUserBinding(Base):
    __tablename__ = "accounting_platform_user_bindings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # e.g., 'telegram'
    platform_user_id: Mapped[str] = mapped_column(
        String(100), index=True, nullable=False
    )
