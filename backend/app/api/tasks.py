from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel
from typing import Any, Optional
from app.tasks.manager import manager
from app.tasks.models import TaskPriority, TaskStatus
from app.actions.models import ContextInput
from app.actions.registry import registry as action_registry

router = APIRouter(prefix='/tasks', tags=['tasks'])

class SubmitTaskRequest(BaseModel):
    action_id: str
    context: ContextInput
    params: dict = {}
    priority: str | None = None  # 'high' | 'normal' | 'low'

class SubmitTaskResponse(BaseModel):
    task_id: str
    status: str

@router.post('/submit', response_model=SubmitTaskResponse)
async def submit_task(payload: SubmitTaskRequest):
    ctx = payload.context
    resolved = action_registry.resolve(payload.action_id, ctx)
    if not resolved:
        raise HTTPException(status_code=404, detail='Action not found')
    definition, handler = resolved
    # Map priority string
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
    result_kind: str
    error: str | None
    result: Any | None

@router.get('/{task_id}', response_model=TaskResponse)
async def get_task(task_id: str):
    task = manager.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')
    return TaskResponse(
        id=task.id,
        action_id=task.action_id,
        service=task.service,
        priority=task.priority.name,
        status=task.status.value,
        result_kind=task.result_kind,
        error=task.error,
        result=task.result,
    )

@router.post('/{task_id}/cancel')
async def cancel_task(task_id: str):
    ok = manager.cancel(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail='Task not found or cannot cancel')
    return {'status': 'ok'}

class ListTasksResponse(BaseModel):
    tasks: list[TaskResponse]

@router.get('', response_model=ListTasksResponse)
async def list_tasks(service: str | None = None, status: str | None = None):
    st = TaskStatus(status) if status in TaskStatus.__members__.values() else None  # type: ignore
    tasks = manager.list(service=service, status=st)
    return ListTasksResponse(tasks=[TaskResponse(
        id=t.id,
        action_id=t.action_id,
        service=t.service,
        priority=t.priority.name,
        status=t.status.value,
        result_kind=t.result_kind,
        error=t.error,
        result=t.result,
    ) for t in tasks])
