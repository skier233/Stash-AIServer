from __future__ import annotations
from typing import Dict, List
from app.actions.registry import registry as action_registry, collect_actions


class ServiceBase:
    name: str = 'unnamed'
    description: str = ''
    server_url: str | None = None  # external server backing this logical service
    max_concurrency: int = 1
    # Optional default priorities (could be used later for auto-priority decisions)
    default_single_priority: str = 'high'
    default_bulk_priority: str = 'low'

    def connectivity(self) -> str:
        # Placeholder: later implement health ping / handshake
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
        # Configure task manager (if present) dynamically
        try:
            from app.tasks.manager import manager as task_manager
            task_manager.configure_service(service.name, service.max_concurrency, service.server_url)
        except Exception:
            # Task system optional at this stage
            pass

    def list(self) -> List[ServiceBase]:
        return list(self._services.values())

    def get(self, name: str) -> ServiceBase | None:
        return self._services.get(name)


services = ServiceRegistry()
