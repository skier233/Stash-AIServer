from __future__ import annotations
from typing import Dict, Tuple, List, Any, Callable
from .models import RecommenderDefinition, RecommenderHandler, RecContext, RecommendationRequest

class _RecommenderRegistry:
    def __init__(self):
        self._defs: Dict[str, Tuple[RecommenderDefinition, RecommenderHandler]] = {}

    def register(self, definition: RecommenderDefinition, handler: RecommenderHandler):
        if definition.id in self._defs:
            raise ValueError(f"Recommender already registered: {definition.id}")
        self._defs[definition.id] = (definition, handler)

    def list_for_context(self, ctx: RecContext) -> List[RecommenderDefinition]:
        return [d for d, _ in self._defs.values() if ctx in d.contexts]

    def get(self, id: str) -> Tuple[RecommenderDefinition, RecommenderHandler] | None:
        return self._defs.get(id)

    def unregister_by_module_prefix(self, prefix: str) -> None:
        if not prefix:
            return
        remove_ids = [rid for rid, (_, handler) in self._defs.items() if getattr(handler, '__module__', '').startswith(prefix)]
        for rid in remove_ids:
            self._defs.pop(rid, None)

recommender_registry = _RecommenderRegistry()

def recommender(*, id: str, label: str, contexts: List[RecContext], description: str = "", config: List[Any] | None = None, **caps):
    def wrapper(fn: RecommenderHandler):
        definition = RecommenderDefinition(id=id, label=label, contexts=contexts, description=description, config=config or [], **caps)
        recommender_registry.register(definition, fn)
        return fn
    return wrapper

## Legacy autodiscovery removed; plugin loader now imports recommender modules explicitly.
