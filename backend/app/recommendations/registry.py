from __future__ import annotations
from typing import Dict, Tuple, List, Any, Callable
from importlib import import_module
import pkgutil, pathlib, importlib.util, sys
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

recommender_registry = _RecommenderRegistry()

def recommender(*, id: str, label: str, contexts: List[RecContext], description: str = "", config: List[Any] | None = None, **caps):
    def wrapper(fn: RecommenderHandler):
        definition = RecommenderDefinition(id=id, label=label, contexts=contexts, description=description, config=config or [], **caps)
        recommender_registry.register(definition, fn)
        return fn
    return wrapper

# Auto-discovery utility

def autodiscover(base_package: str = 'app.recommendations.recommenders'):
    """Discover recommender submodules.

    Handles both regular and namespace packages. If no recommenders are
    registered after discovery, emits a diagnostic to stdout so users can
    understand why the list is empty.
    """
    try:
        pkg = import_module(base_package)
    except Exception as e:  # pragma: no cover - defensive
        print(f'[recommenders] autodiscover import failed for {base_package}: {e}', flush=True)
        return

    search_paths: list[str] = []
    pkg_file = getattr(pkg, '__file__', None)
    if pkg_file:
        search_paths.append(str(pathlib.Path(pkg_file).parent))
    # Namespace package support
    spec = importlib.util.find_spec(base_package)
    if spec and spec.submodule_search_locations:  # type: ignore[attr-defined]
        for p in spec.submodule_search_locations:  # type: ignore
            if p not in search_paths:
                search_paths.append(p)

    if not search_paths:
        print(f'[recommenders] no search paths for {base_package} (namespace without locations)', flush=True)
        return

    seen = set()
    for path in search_paths:
        for mod in pkgutil.walk_packages([path], prefix=pkg.__name__ + '.'):
            if mod.name in seen:  # avoid re-import duplicates across paths
                continue
            seen.add(mod.name)
            try:
                import_module(mod.name)
            except Exception as e:
                print(f'[recommenders] failed import {mod.name}: {e}', flush=True)

    if not recommender_registry._defs:
        print(f'[recommenders] WARN: no recommenders registered after autodiscover (paths={search_paths})', flush=True)
