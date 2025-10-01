from __future__ import annotations
from typing import Callable, Dict, List, Optional
from .models import ActionDefinition, ActionHandler, ContextRule, ContextInput


class ActionRegistry:
    """Simplified registry: actions stored as flat lists per logical id."""
    def __init__(self):
        self._actions: Dict[str, List[ActionDefinition]] = {}
        self._handlers: Dict[str, List[ActionHandler]] = {}

    def register(self, definition: ActionDefinition, handler: ActionHandler):
        self._actions.setdefault(definition.id, [])
        self._handlers.setdefault(definition.id, [])
        self._actions[definition.id].append(definition)
        self._handlers[definition.id].append(handler)

    def list_all(self) -> List[ActionDefinition]:
        out: List[ActionDefinition] = []
        for defs in self._actions.values():
            out.extend(defs)
        return out

    def list_ids(self) -> List[str]:
        return list(self._actions.keys())

    def resolve(self, action_id: str, ctx: ContextInput) -> Optional[tuple[ActionDefinition, ActionHandler]]:
        defs = self._actions.get(action_id)
        handlers = self._handlers.get(action_id)
        if not defs or not handlers:
            return None
        # Choose first applicable definition; prefer detail-view-specific when on detail, else non-detail.
        preferred_kind = 'detail' if ctx.is_detail_view else 'library'
        chosen: Optional[int] = None
        for idx, d in enumerate(defs):
            if d.is_applicable(ctx):
                kind = 'detail' if any(r.selection == 'single' for r in d.contexts) else 'library'
                if kind == preferred_kind:
                    return d, handlers[idx]
                if chosen is None:
                    chosen = idx
        if chosen is not None:
            return defs[chosen], handlers[chosen]
        return None

    def all_for_id(self, action_id: str) -> List[ActionDefinition]:
        return self._actions.get(action_id, [])


registry = ActionRegistry()


def action(
    *,
    id: str,
    label: str,
    description: str = '',
    service: str,
    result_kind: str = 'none',
    contexts: Optional[list[ContextRule]] = None,
    input_schema: dict | None = None,
    controller: bool = False,
):
    """Decorator to declare an action on a service class method.

    The decorated function signature should be (ctx: ContextInput, params: dict) -> Any (sync or async).
    """
    def decorator(fn):
        definition = ActionDefinition(
            id=id,
            label=label,
            description=description,
            service=service,
            result_kind=result_kind,
            contexts=contexts or [],
            input_schema=input_schema,
            controller=controller,
        )
        # Attach metadata for later collection when service registers
        setattr(fn, '_action_definition', definition)
        return fn
    return decorator


def collect_actions(obj) -> list[tuple[ActionDefinition, ActionHandler]]:
    pairs = []
    for attr_name in dir(obj):
        attr = getattr(obj, attr_name)
        definition = getattr(attr, '_action_definition', None)
        if definition:
            # Bind method to instance if it's a function on the object
            if callable(attr):
                handler = attr  # already bound if accessed via instance
                pairs.append((definition, handler))
    return pairs
