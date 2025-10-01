from __future__ import annotations
from sqlalchemy import String, Integer, Float, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from stash_ai_server.db.session import Base


class TaskHistory(Base):
    """Immutable record of top-level task terminal states for recent history UI.

    Only parent/controller tasks are persisted; children (group members) are excluded
    to keep history concise and bounded. Pruning handled in manager.
    """
    __tablename__ = 'task_history'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True, unique=True)
    action_id: Mapped[str] = mapped_column(String(200))
    service: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(50))  # completed / failed / cancelled
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    submitted_at: Mapped[float] = mapped_column(Float)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    items_sent: Mapped[int | None] = mapped_column(Integer, nullable=True)  # count of items/spawned children
    item_id: Mapped[str | None] = mapped_column(String(200), nullable=True)  # single item identifier if applicable
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    def as_dict(self) -> dict:
        return {
            'task_id': self.task_id,
            'action_id': self.action_id,
            'service': self.service,
            'status': self.status,
            'submitted_at': self.submitted_at,
            'started_at': self.started_at,
            'finished_at': self.finished_at,
            'duration_ms': self.duration_ms,
            'items_sent': self.items_sent,
            'item_id': self.item_id,
            'error': self.error,
        }
