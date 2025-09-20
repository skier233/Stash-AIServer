from __future__ import annotations
from typing import Callable, Any, Literal, Optional, List
from pydantic import BaseModel, Field

# -----------------------------------------------------------------------------
# Action & Context Models
# -----------------------------------------------------------------------------

SelectionMode = Literal['single', 'multi', 'both']


class ContextInput(BaseModel):
    page: str
    entity_id: Optional[str] = Field(None, alias='entityId')
    is_detail_view: bool = Field(False, alias='isDetailView')
    selected_ids: Optional[List[str]] = Field(None, alias='selectedIds')

    model_config = {
        'populate_by_name': True,
        'extra': 'ignore'
    }

class ContextRule(BaseModel):
    pages: List[str] = Field(default_factory=list, description="Allowed page keys (empty = any)")
    allow_detail: Optional[bool] = Field(None, description="If set, require detail (True) or list (False)")
    selection: SelectionMode = 'both'  # how selection list is treated
    min_selected: int = 0
    max_selected: Optional[int] = None

    def matches(self, ctx: ContextInput) -> bool:
        # Page match
        if self.pages and ctx.page not in self.pages:
            return False
        # Detail / list constraint
        if self.allow_detail is not None and ctx.is_detail_view != self.allow_detail:
            return False
        # Selection constraints
        sel_count = len(ctx.selected_ids or [])
        if self.selection == 'single' and sel_count > 1:
            return False
        if self.selection == 'multi' and sel_count <= 1:
            return False
        if sel_count < self.min_selected:
            return False
        if self.max_selected is not None and sel_count > self.max_selected:
            return False
        return True


class ActionDefinition(BaseModel):
    id: str
    label: str
    description: str = ''
    service: str
    result_kind: Literal['none', 'dialog', 'notification', 'stream'] = 'none'
    contexts: List[ContextRule] = Field(default_factory=list)
    input_schema: Optional[dict] = None
    # internal: handler callable stored out-of-band in registry

    def is_applicable(self, ctx: ContextInput) -> bool:
        if not self.contexts:
            return True
        return any(rule.matches(ctx) for rule in self.contexts)


# Type alias for action handlers (first argument is the raw context payload + params)
ActionHandler = Callable[[ContextInput, dict], Any]
