from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from api.core.database import Base


class Camera(Base):
    __tablename__ = "cameras"
    __table_args__ = (
        UniqueConstraint("mediamtx_path", name="uq_cameras_mediamtx_path"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    mediamtx_path: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    rtsp_url_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    onvif_enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    onvif_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    onvif_port: Mapped[int | None] = mapped_column(nullable=True)
    onvif_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    onvif_password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
