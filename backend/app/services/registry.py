from __future__ import annotations

from typing import Dict, List
from app.actions.registry import registry as action_registry, collect_actions

# Optional task manager: not required in minimal setups
try:
    from app.tasks.manager import manager as task_manager
except Exception:
    task_manager = None


class ServiceBase:
    name: str = 'unnamed'
    description: str = ''
    server_url: str | None = None
    max_concurrency: int = 1
    # Optional default priorities
    default_single_priority: str = 'high'
    default_bulk_priority: str = 'low'

    def connectivity(self) -> str:
        return 'unknown'


class ServiceRegistry:
    def __init__(self):
        self._services: Dict[str, ServiceBase] = {}

    def register(self, service: ServiceBase):
        if service.name in self._services:
            raise ValueError(f"Service already registered: {service.name}")
        self._services[service.name] = service
        # Collect actions via decorator metadata
        for definition, handler in collect_actions(service):
            # patch service field if not provided
            if not definition.service:
                definition.service = service.name
            action_registry.register(definition, handler)
        # Configure task manager dynamically if available
        try:
            if task_manager is not None:
                task_manager.configure_service(service.name, service.max_concurrency, service.server_url)
        except Exception:
            # Task system optional at this stage
            pass

    def list(self) -> List[ServiceBase]:
        return list(self._services.values())

    def get(self, name: str) -> ServiceBase | None:
        return self._services.get(name)


services = ServiceRegistry()
