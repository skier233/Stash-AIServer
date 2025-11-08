from fastapi import APIRouter, Body, HTTPException
from stash_ai_server.actions.registry import registry as action_registry
from stash_ai_server.actions.models import ContextInput, ActionDefinition
from stash_ai_server.tasks.manager import manager as task_manager
from stash_ai_server.tasks.models import TaskPriority
from pydantic import BaseModel
from typing import Any, Optional, Dict

router = APIRouter(prefix='/actions', tags=['actions'])


class ActionsAvailableResponse(ActionDefinition):
    pass


@router.post('/available', response_model=list[ActionsAvailableResponse])
async def list_available_actions(context: ContextInput = Body(..., embed=True)):
    """Return actions applicable to provided context."""
    results: list[ActionDefinition] = []
    for definition in action_registry.list_all():
        if definition.is_applicable(context):
            results.append(definition)
    return results


class SubmitActionRequest(BaseModel):
    action_id: str
    context: ContextInput
    params: Dict[str, Any] = {}
    priority: str | None = None  # optional override

class SubmitActionResponse(BaseModel):
    task_id: str
    status: str
    inferred_priority: str

@router.post('/submit', response_model=SubmitActionResponse)
async def submit_action(payload: SubmitActionRequest):
    ctx = payload.context
    resolved = action_registry.resolve(payload.action_id, ctx)
    if not resolved:
        raise HTTPException(status_code=404, detail='Action not found')
    definition, handler = resolved
    if not definition.is_applicable(ctx):
        raise HTTPException(status_code=400, detail='Action not applicable to provided context')
    if definition.deduplicate_submissions:
        duplicate = task_manager.find_duplicate(definition, handler, ctx, payload.params)
        if duplicate is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    'code': 'ACTION_ALREADY_IN_PROGRESS',
                    'task_id': duplicate.id,
                    'status': duplicate.status.value,
                    'message': f"Action '{definition.label}' is already processing for this selection.",
                },
            )
    # Infer priority (detail -> high, bulk -> low) unless overridden.
    inferred = 'high' if ctx.is_detail_view else 'low'
    if payload.priority in ('high', 'normal', 'low'):
        inferred = payload.priority
    prio_map = {'high': TaskPriority.high, 'normal': TaskPriority.normal, 'low': TaskPriority.low}
    task = task_manager.submit(definition, handler, ctx, payload.params, prio_map[inferred])
    return SubmitActionResponse(task_id=task.id, status=task.status.value, inferred_priority=inferred)


"""Synchronous execution removed: all actions must be enqueued via /tasks/submit (or future /actions/submit wrapper)."""
