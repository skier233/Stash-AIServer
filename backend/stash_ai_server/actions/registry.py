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

    def unregister_service(self, service_name: str) -> None:
        if not service_name:
            return
        for action_id in list(self._actions.keys()):
            defs = self._actions.get(action_id, [])
            handlers = self._handlers.get(action_id, [])
            if not defs:
                continue
            keep_defs: List[ActionDefinition] = []
            keep_handlers: List[ActionHandler] = []
            for definition, handler in zip(defs, handlers):
                if getattr(definition, 'service', None) == service_name:
                    continue
                keep_defs.append(definition)
                keep_handlers.append(handler)
            if keep_defs:
                self._actions[action_id] = keep_defs
                self._handlers[action_id] = keep_handlers
            else:
                self._actions.pop(action_id, None)
                self._handlers.pop(action_id, None)


registry = ActionRegistry()


def action(
    *,
    id: str,
    label: str,
    description: str = '',
    service: str | None = None,
    result_kind: str = 'none',
    dialog_type: Optional[str] = None,
    contexts: Optional[list[ContextRule]] = None,
    input_schema: dict | None = None,
    deduplicate_submissions: bool = True,
):
    """Decorator to declare an action on a service class method.

    The decorated function signature should be (ctx: ContextInput, params: dict) -> Any (sync or async).
    """
    def decorator(fn):
        definition = ActionDefinition(
            id=id,
            label=label,
            description=description,
            service=service or '',
            result_kind=result_kind,
            dialog_type=dialog_type,
            contexts=contexts or [],
            input_schema=input_schema,
            deduplicate_submissions=deduplicate_submissions,
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
