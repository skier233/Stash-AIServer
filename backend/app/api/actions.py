from fastapi import APIRouter, Body, HTTPException
from app.actions.registry import registry as action_registry
from app.actions.models import ContextInput, ActionDefinition
from pydantic import BaseModel
from typing import Any, Optional, Dict

router = APIRouter(prefix='/actions', tags=['actions'])


class ActionsAvailableResponse(ActionDefinition):
    # When listing available actions we present the context-resolved variant only.
    pass


@router.post('/available', response_model=list[ActionsAvailableResponse])
async def list_available_actions(context: ContextInput = Body(..., embed=True)):
    """Return actions applicable to provided context.

    Body shape: { "context": { ...pageContextFields }}
    """
    # Simplified: just collect all registered action definitions whose own context rules match.
    # This naturally yields only the single variant on detail pages and only the bulk variant on library pages
    # because their ContextRule.matches() are mutually exclusive (detail vs non-detail).
    results: list[ActionDefinition] = []
    for definition in action_registry.list_all():
        if definition.is_applicable(context):
            results.append(definition)
    return results


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
    ctx = payload.context
    resolved = action_registry.resolve(payload.action_id, ctx)
    if not resolved:
        raise HTTPException(status_code=404, detail='Action not found')
    definition, handler = resolved
    if not definition.is_applicable(ctx):
        raise HTTPException(status_code=400, detail='Action not applicable to provided context')
    result = await handler(ctx, payload.params)  # type: ignore
    return ExecuteActionResponse(
        action_id=definition.id,
        result_kind=definition.result_kind,
        result=result
    )
