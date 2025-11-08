from __future__ import annotations

import logging
from typing import Any, Dict, List
from stash_ai_server.actions.registry import registry as action_registry, collect_actions
from stash_ai_server.core.runtime import register_backend_refresh_handler

from stash_ai_server.db.session import SessionLocal
from stash_ai_server.models.plugin import PluginSetting

try:
    from stash_ai_server.tasks.manager import manager as _initial_task_manager
except Exception:
    _initial_task_manager = None


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
        self._task_manager = _initial_task_manager
        self._pending_task_configs: set[str] = set()

    def register(self, service: ServiceBase):
        if service.name in self._services:
            self.unregister(service.name)
        self._services[service.name] = service
        # Collect actions via decorator metadata
        for definition, handler in collect_actions(service):
            definition.service = service.name
            action_registry.register(definition, handler)
        self._ensure_task_manager()
        self._configure_service(service)

    def list(self) -> List[ServiceBase]:
        return list(self._services.values())

    def get(self, name: str) -> ServiceBase | None:
        return self._services.get(name)

    def unregister(self, service_name: str) -> None:
        service = self._services.pop(service_name, None)
        if service is None:
            return
        action_registry.unregister_service(service.name)
        pending = self._pending_task_configs
        if service_name in pending:
            pending.discard(service_name)
        manager = self._task_manager
        if manager is not None:
            try:
                manager.remove_service(service_name)
            except Exception:
                pass

    def unregister_by_plugin(self, plugin_name: str) -> None:
        targets = [name for name, svc in self._services.items() if getattr(svc, 'plugin_name', None) == plugin_name]
        for service_name in targets:
            self.unregister(service_name)

    def set_task_manager(self, manager) -> None:
        if manager is None:
            return
        self._task_manager = manager
        self._configure_pending_services()

    def _ensure_task_manager(self):
        if self._task_manager is not None:
            return
        try:
            from stash_ai_server.tasks.manager import manager as _task_manager
        except Exception:
            _task_manager = None
        if _task_manager is not None:
            self.set_task_manager(_task_manager)

    def _configure_service(self, service: ServiceBase) -> None:
        manager = self._task_manager
        if manager is None:
            self._pending_task_configs.add(service.name)
            return
        try:
            manager.configure_service(service.name, service.max_concurrency, service.server_url)
            self._pending_task_configs.discard(service.name)
        except Exception:
            # Keep the service pending so a future attempt can retry once dependencies settle.
            self._pending_task_configs.add(service.name)

    def _configure_pending_services(self) -> None:
        if self._task_manager is None:
            return
        for name in list(self._pending_task_configs):
            service = self._services.get(name)
            if service is None:
                self._pending_task_configs.discard(name)
                continue
            try:
                self._task_manager.configure_service(service.name, service.max_concurrency, service.server_url)
                self._pending_task_configs.discard(name)
            except Exception:
                # Leave in pending for the next opportunity.
                pass


services = ServiceRegistry()


def _refresh_registered_services() -> None:
    for service in services.list():
        reload_cb = getattr(service, 'reload_settings', None)
        if callable(reload_cb):
            try:
                reload_cb()
            except Exception:  # pragma: no cover - defensive logging
                _log.exception("service refresh failed for %s", service.name)
                continue
        try:
            services._configure_service(service)
        except Exception:  # pragma: no cover - defensive logging
            _log.exception("service reconfiguration failed for %s", service.name)


register_backend_refresh_handler('services', _refresh_registered_services)
