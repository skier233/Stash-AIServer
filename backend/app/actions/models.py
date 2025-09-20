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
    selection: SelectionMode = 'both'

    # Simplified semantics per user request:
    #   We ignore actual selection counts entirely.
    #   Library (list) view => "bulk" mode, Detail view => "single" mode.
    #   Mapping of existing field 'selection':
    #       'single' -> only matches detail view
    #       'multi'  -> only matches library view
    #       'both'   -> only matches library view (acts as bulk)
    #   (Selection count constraints removed for simplicity.)
    def matches(self, ctx: ContextInput) -> bool:
        if self.pages and ctx.page not in self.pages:
            return False
        if self.selection == 'single':
            return ctx.is_detail_view
        # multi or both => library (non-detail) view
        return not ctx.is_detail_view


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

    # Derived variant classification (not user-specified) used by registry for multi-variant resolution.
    # If any rule has selection='single' and no rule has selection='both' -> single
    # If any rule has selection='both' and no rule has selection='single' -> bulk
    # If both present treat as distinct variants; registry will store two separate defs under same id.
    # If neither (empty contexts) => generic.
    def derived_variant_key(self) -> str:
        if not self.contexts:
            return 'generic'
        selections = {r.selection for r in self.contexts}
        if selections == {'single'}:
            return 'single'
        if selections == {'both'} or selections == {'multi'}:
            return 'bulk'
        # mixed or other => generic (could extend for multi-specific in future)
        return 'generic'


# Type alias for action handlers (first argument is the raw context payload + params)
ActionHandler = Callable[[ContextInput, dict], Any]
