from fastapi import APIRouter, Body, HTTPException
from app.actions.registry import registry as action_registry
from app.actions.models import ContextInput, ActionDefinition
from pydantic import BaseModel
from typing import Any, Optional, Dict

router = APIRouter(prefix='/actions', tags=['actions'])


class ActionsAvailableResponse(ActionDefinition):
    pass


@router.post('/available', response_model=list[ActionsAvailableResponse])
async def list_available_actions(context: ContextInput = Body(..., embed=True)):
    """Return actions applicable to provided context.

    Body shape: { "context": { ...pageContextFields }}
    """
    applicable = [a for a in action_registry.list() if a.is_applicable(context)]
    return applicable


class ExecuteActionRequest(BaseModel):
    action_id: str
    context: ContextInput
    params: Dict[str, Any] = {}

class ExecuteActionResponse(BaseModel):
    action_id: str
    result_kind: str
    result: Any | None = None

@router.post('/execute', response_model=ExecuteActionResponse)
async def execute_action(payload: ExecuteActionRequest):
    definition = action_registry.get(payload.action_id)
    if not definition:
        raise HTTPException(status_code=404, detail='Action not found')
    if not definition.is_applicable(payload.context):
        raise HTTPException(status_code=400, detail='Action not applicable to provided context')
    handler = action_registry.handler_for(payload.action_id)
    if not handler:
        raise HTTPException(status_code=500, detail='Handler missing for action')
    # Execute directly (stub synchronous execution). Later will enqueue task.
    result = await handler(payload.context, payload.params)  # type: ignore
    # Normalize raw result if handler returned plain value
    return ExecuteActionResponse(action_id=definition.id, result_kind=definition.result_kind, result=result)
