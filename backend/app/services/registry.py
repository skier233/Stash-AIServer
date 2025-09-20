from __future__ import annotations
from typing import Dict, List
from app.actions.registry import registry as action_registry, collect_actions


class ServiceBase:
    name: str = 'unnamed'
    description: str = ''
    server_url: str | None = None  # external server backing this logical service

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

    def list(self) -> List[ServiceBase]:
        return list(self._services.values())

    def get(self, name: str) -> ServiceBase | None:
        return self._services.get(name)


services = ServiceRegistry()
