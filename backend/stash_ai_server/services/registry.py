from __future__ import annotations

import logging
from typing import Any, Dict, List
from stash_ai_server.actions.registry import registry as action_registry, collect_actions

from stash_ai_server.db.session import SessionLocal
from stash_ai_server.models.plugin import PluginSetting

# Optional task manager: not required in minimal setups
try:
    from stash_ai_server.tasks.manager import manager as task_manager
except Exception:
    task_manager = None


_log = logging.getLogger(__name__)

class ServiceBase:
    name: str = 'unnamed'
    description: str = ''
    server_url: str | None = None
    max_concurrency: int = 1
    # Optional default priorities
    default_single_priority: str = 'high'
    default_bulk_priority: str = 'low'
    plugin_name: str | None = None

    def __init__(self) -> None:
        self.plugin_name = self._resolve_plugin_name()

    def _resolve_plugin_name(self) -> str:
        explicit = getattr(self, 'plugin_name', None)
        if explicit:
            return explicit
        module = self.__class__.__module__ or ''
        parts = module.split('.')
        try:
            idx = parts.index('plugins')
            if idx + 1 < len(parts):
                candidate = parts[idx + 1]
                if candidate:
                    return candidate
        except ValueError:
            pass
        return self.name

    def connectivity(self) -> str:
        return 'unknown'

    def _load_settings(self) -> dict[str, Any]:
        try:
            db = SessionLocal()
        except Exception as exc:  # pragma: no cover - database unavailable
            _log.error("Unable to open session for settings: %s", exc)
            return {}
        try:
            rows = (
                db.query(PluginSetting)
                .filter(PluginSetting.plugin_name == self.plugin_name)
                .all()
            )
            settings: dict[str, Any] = {}
            for row in rows:
                value = row.value if row.value is not None else row.default_value
                if value is None:
                    continue
                settings[row.key] = value
            return settings
        except Exception as exc:  # pragma: no cover - defensive
            _log.error("Failed loading plugin settings: %s", exc)
            return {}
        finally:
            try:
                db.close()
            except Exception:
                pass


class ServiceRegistry:
    def __init__(self):
        self._services: Dict[str, ServiceBase] = {}

    def register(self, service: ServiceBase):
        if service.name in self._services:
            raise ValueError(f"Service already registered: {service.name}")
        self._services[service.name] = service
        # Collect actions via decorator metadata
        for definition, handler in collect_actions(service):
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
