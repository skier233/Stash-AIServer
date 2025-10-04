from fastapi import APIRouter, Body, HTTPException, Depends
from pydantic import BaseModel
from typing import Any, Optional
from sqlalchemy.orm import Session
from stash_ai_server.db.session import get_db
from stash_ai_server.tasks.manager import manager
from stash_ai_server.tasks.models import TaskPriority, TaskStatus
from stash_ai_server.tasks.history import TaskHistory
from stash_ai_server.actions.models import ContextInput
from stash_ai_server.actions.registry import registry as action_registry

router = APIRouter(prefix='/tasks', tags=['tasks'])

class SubmitTaskRequest(BaseModel):
    action_id: str
    context: ContextInput
    params: dict = {}
    priority: str | None = None  # 'high' | 'normal' | 'low'

class SubmitTaskResponse(BaseModel):
    task_id: str
    status: str

# Keep '/history' route defined before '/{task_id}' to avoid route capture issues.
class HistoryItem(BaseModel):
    task_id: str
    action_id: str
    service: str
    status: str
    submitted_at: float
    started_at: float | None
    finished_at: float | None
    duration_ms: int | None
    items_sent: int | None
    item_id: str | None
    error: str | None

@router.get('/history')
def task_history(limit: int = 50, service: str | None = None, status: str | None = None, db: Session = Depends(get_db)) -> dict:
    """Return recent task history (newest first)."""
    q = db.query(TaskHistory)
    if service:
        q = q.filter(TaskHistory.service == service)
    if status:
        q = q.filter(TaskHistory.status == status)
    rows = q.order_by(TaskHistory.created_at.desc()).limit(min(limit, 500)).all()
    return {'history': [r.as_dict() for r in rows]}

@router.post('/submit', response_model=SubmitTaskResponse)
async def submit_task(payload: SubmitTaskRequest):
    """Resolve action and submit a task to the manager."""
    ctx = payload.context
    resolved = action_registry.resolve(payload.action_id, ctx)
    if not resolved:
        raise HTTPException(status_code=404, detail='Action not found')
    definition, handler = resolved
    prio = TaskPriority.normal
    if payload.priority == 'high':
        prio = TaskPriority.high
    elif payload.priority == 'low':
        prio = TaskPriority.low
    task = manager.submit(definition, handler, ctx, payload.params, prio)
    return SubmitTaskResponse(task_id=task.id, status=task.status.value)

class TaskResponse(BaseModel):
    id: str
    action_id: str
    service: str
    priority: str
    status: str
    error: str | None
    result: Any | None
    group_id: str | None
    started_at: float | None = None
    finished_at: float | None = None

@router.get('/{task_id}', response_model=TaskResponse)
async def get_task(task_id: str):
    """Fetch current state (in-memory) of a task by id."""
    task = manager.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')
    return TaskResponse(
        id=task.id,
        action_id=task.action_id,
        service=task.service,
        priority=task.priority.name,
        status=task.status.value,
        error=task.error,
        result=task.result,
        group_id=task.group_id,
        started_at=task.started_at,
        finished_at=task.finished_at
    )

@router.post('/{task_id}/cancel')
async def cancel_task(task_id: str):
    """Attempt to cancel a queued or running task; cascades to children."""
    ok = manager.cancel(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail='Task not found or cannot cancel')
    return {'status': 'ok'}

class ListTasksResponse(BaseModel):
    tasks: list[TaskResponse]

@router.get('', response_model=ListTasksResponse)
async def list_tasks(service: str | None = None, status: str | None = None):
    """List current in-memory tasks (optionally filter by service/status)."""
    st = None
    if status:
        try:
            st = TaskStatus(status)
        except ValueError:
            st = None
    tasks = manager.list(service=service, status=st)
    return ListTasksResponse(tasks=[TaskResponse(
        id=t.id,
        action_id=t.action_id,
        service=t.service,
        priority=t.priority.name,
        status=t.status.value,
        error=t.error,
        result=t.result,
        group_id=t.group_id,
        started_at=t.started_at,
        finished_at=t.finished_at
    ) for t in tasks])

## Removed temporary '/tasks/history' alias after route ordering fix; canonical path is '/api/v1/tasks/history'.
