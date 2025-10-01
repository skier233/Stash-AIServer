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
    controller: bool = False  # if true, task is lightweight and should not count against concurrency
    # internal: handler callable stored out-of-band in registry

    def is_applicable(self, ctx: ContextInput) -> bool:
        if not self.contexts:
            return True
        return any(rule.matches(ctx) for rule in self.contexts)

    # Variant derivation removed; registry now stores plain lists per action id.
    def variant_kind(self) -> str:
        # Lightweight helper if needed by clients; not used in registry logic.
        if not self.contexts:
            return 'generic'
        selections = {r.selection for r in self.contexts}
        # Map exactly one mode; treat 'both' as generic (since it's ambiguous under simplified semantics).
        if selections == {'single'}:
            return 'single'
        if selections == {'multi'}:
            return 'bulk'
        # Any presence of 'both', or mixed combinations => generic
        return 'generic'


# Type alias for action handlers (first argument is the raw context payload + params)
ActionHandler = Callable[[ContextInput, dict], Any]
