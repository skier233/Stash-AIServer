from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import Integer, String, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base

class PluginMeta(Base):
    __tablename__ = 'plugin_meta'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    required_backend: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default='active')  # active|error|incompatible
    migration_head: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc), nullable=False)
