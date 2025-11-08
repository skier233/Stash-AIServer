from __future__ import annotations
import enum
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional, List, Dict
from pydantic import BaseModel
from stash_ai_server.actions.models import ContextInput

#TODO: Add more priority options
class TaskPriority(enum.IntEnum):
    high = 0
    normal = 10
    low = 20

class TaskStatus(str, enum.Enum):
    queued = 'queued'
    running = 'running'
    completed = 'completed'
    failed = 'failed'
    cancelled = 'cancelled'
    streaming = 'streaming'

class TaskRecord(BaseModel):
    id: str
    action_id: str
    service: str
    priority: TaskPriority
    status: TaskStatus
    submitted_at: float
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    context: ContextInput
    params: Dict[str, Any]
    result: Any | None = None
    error: str | None = None
    cancel_requested: bool = False
    chunks: List[Any] = []  # for streaming accumulation (optional)
    group_id: str | None = None  # logical parent grouping (e.g., batch parent task id)
    skip_concurrency: bool = False  # controller/coordination tasks that shouldn't consume a slot
    dedupe_ctx_key: str | None = None
    dedupe_params_key: str | None = None

    class Config:
        arbitrary_types_allowed = True

    def summary(self) -> dict:
        return {
            'id': self.id,
            'action_id': self.action_id,
            'service': self.service,
            'priority': self.priority.name,
            'status': self.status.value,
            'submitted_at': self.submitted_at,
            'started_at': self.started_at,
            'finished_at': self.finished_at,
            'error': self.error,
            'cancel_requested': self.cancel_requested,
            'result': self.result if self.status == TaskStatus.completed else None,
            'group_id': self.group_id,
        }

class CancelToken:
    def __init__(self):
        self._cancelled = False
    def request(self):
        self._cancelled = True
    def is_cancelled(self) -> bool:
        return self._cancelled


def new_task(action_id: str, service: str, priority: TaskPriority, ctx: ContextInput, params: dict) -> TaskRecord:
    return TaskRecord(
        id=str(uuid.uuid4()),
        action_id=action_id,
        service=service,
        priority=priority,
        status=TaskStatus.queued,
        submitted_at=time.time(),
        context=ctx,
        params=params,
        dedupe_ctx_key=None,
        dedupe_params_key=None,
    )


@dataclass
class TaskSpec:
    id: str
    service: str = ''
