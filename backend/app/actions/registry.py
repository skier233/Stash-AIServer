from __future__ import annotations
from typing import Callable, Dict, List, Optional
from .models import ActionDefinition, ActionHandler, ContextRule, ContextInput


class ActionRegistry:
    """Central store supporting multiple variants per logical action id.

    Data shape:
      _actions[id][variant] = ActionDefinition
      _handlers[id][variant] = ActionHandler
    """
    def __init__(self):
        self._actions: Dict[str, Dict[str, ActionDefinition]] = {}
        self._handlers: Dict[str, Dict[str, ActionHandler]] = {}

    def register(self, definition: ActionDefinition, handler: ActionHandler):
        self._actions.setdefault(definition.id, {})
        self._handlers.setdefault(definition.id, {})
        variant_key = definition.derived_variant_key()
        if variant_key in self._actions[definition.id]:
            raise ValueError(f"Duplicate registration for {definition.id} variant={variant_key}")
        self._actions[definition.id][variant_key] = definition
        self._handlers[definition.id][variant_key] = handler

    def list_all(self) -> List[ActionDefinition]:
        out: List[ActionDefinition] = []
        for variants in self._actions.values():
            out.extend(variants.values())
        return out

    def list(self) -> List[ActionDefinition]:
        """Return the canonical representative (preferring generic, else single, else bulk)."""
        reps: List[ActionDefinition] = []
        for vid, variants in self._actions.items():
            if 'generic' in variants:
                reps.append(variants['generic'])
            elif 'single' in variants:
                reps.append(variants['single'])
            elif 'bulk' in variants:
                reps.append(variants['bulk'])
        return reps

    def resolve(self, action_id: str, ctx: ContextInput) -> Optional[tuple[ActionDefinition, ActionHandler]]:
        variants = self._actions.get(action_id)
        if not variants:
            return None
        # Simplified resolution: detail view => single variant; library view => bulk variant.
        if ctx.is_detail_view and 'single' in variants:
            return variants['single'], self._handlers[action_id]['single']
        if not ctx.is_detail_view and 'bulk' in variants:
            return variants['bulk'], self._handlers[action_id]['bulk']
        if 'generic' in variants:
            return variants['generic'], self._handlers[action_id]['generic']
        # Fallback: take any deterministic variant
        first_variant_key = sorted(variants.keys())[0]
        return variants[first_variant_key], self._handlers[action_id][first_variant_key]

    def get_variant(self, action_id: str, variant: str) -> Optional[ActionDefinition]:
        return self._actions.get(action_id, {}).get(variant)

    def handler_for(self, action_id: str, variant: str) -> Optional[ActionHandler]:
        return self._handlers.get(action_id, {}).get(variant)


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
