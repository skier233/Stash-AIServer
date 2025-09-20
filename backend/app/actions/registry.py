from __future__ import annotations
from typing import Callable, Dict, List, Optional
from .models import ActionDefinition, ActionHandler, ContextRule


class ActionRegistry:
    """Central in-memory store for action definitions & handler bindings."""
    def __init__(self):
        self._actions: Dict[str, ActionDefinition] = {}
        self._handlers: Dict[str, ActionHandler] = {}

    def register(self, definition: ActionDefinition, handler: ActionHandler):
        if definition.id in self._actions:
            raise ValueError(f"Action id already registered: {definition.id}")
        self._actions[definition.id] = definition
        self._handlers[definition.id] = handler

    def list(self) -> List[ActionDefinition]:
        return list(self._actions.values())

    def get(self, action_id: str) -> Optional[ActionDefinition]:
        return self._actions.get(action_id)

    def handler_for(self, action_id: str) -> Optional[ActionHandler]:
        return self._handlers.get(action_id)


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
