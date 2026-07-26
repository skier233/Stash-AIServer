"""Unit tests for service discovery, settings and task-manager registration."""

from types import SimpleNamespace

from stash_ai_server.actions.models import ContextRule
from stash_ai_server.actions.registry import action
from stash_ai_server.models.plugin import PluginSetting
from stash_ai_server.services import registry as service_registry


class FakeQuery:
    def __init__(self, rows=(), error=None):
        self.rows = list(rows)
        self.error = error

    def filter(self, *_args):
        if self.error:
            raise self.error
        return self

    def all(self):
        if self.error:
            raise self.error
        return list(self.rows)


class FakeSession:
    def __init__(self, rows=(), error=None, close_error=None):
        self.rows = rows
        self.error = error
        self.close_error = close_error
        self.closed = False

    def query(self, _model):
        return FakeQuery(self.rows, self.error)

    def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


class FakeActionRegistry:
    def __init__(self):
        self.registered = []
        self.unregistered = []

    def register(self, definition, handler):
        self.registered.append((definition, handler))

    def unregister_service(self, service_name):
        self.unregistered.append(service_name)


class FakeTaskManager:
    def __init__(self, *, configure_error=False, remove_error=False):
        self.configure_error = configure_error
        self.remove_error = remove_error
        self.configured = []
        self.removed = []

    def configure_service(self, name, concurrency, server_url):
        if self.configure_error:
            raise RuntimeError("configure failed")
        self.configured.append((name, concurrency, server_url))

    def remove_service(self, name):
        if self.remove_error:
            raise RuntimeError("remove failed")
        self.removed.append(name)


class ExampleService(service_registry.ServiceBase):
    name = "example"
    max_concurrency = 3
    server_url = "http://example.test"

    @action(
        id="example.action",
        label="Example",
        contexts=[ContextRule(pages=["scenes"], selection="single")],
    )
    async def run(self, *_args):
        return None


def test_service_base_resolves_explicit_module_and_fallback_plugin_names():
    class Explicit(service_registry.ServiceBase):
        name = "explicit-service"
        plugin_name = "explicit-plugin"

    class ModuleDerived(service_registry.ServiceBase):
        name = "derived-service"

    class Fallback(service_registry.ServiceBase):
        name = "fallback-service"

    ModuleDerived.__module__ = "stash_ai_server.plugins.sample_plugin.service"

    assert Explicit().plugin_name == "explicit-plugin"
    assert ModuleDerived().plugin_name == "sample_plugin"
    assert Fallback().plugin_name == "fallback-service"
    assert Fallback().connectivity() == "unknown"


def test_service_base_loads_settings_and_closes_session(monkeypatch):
    rows = [
        PluginSetting(
            plugin_name="example",
            key="custom",
            value="value",
            default_value="default",
        ),
        PluginSetting(
            plugin_name="example",
            key="fallback",
            value=None,
            default_value=42,
        ),
        PluginSetting(
            plugin_name="example",
            key="empty",
            value=None,
            default_value=None,
        ),
    ]
    session = FakeSession(rows)
    monkeypatch.setattr(service_registry, "get_session", lambda: session)

    assert ExampleService()._load_settings() == {
        "custom": "value",
        "fallback": 42,
    }
    assert session.closed is True


def test_service_base_handles_session_query_and_close_failures(monkeypatch):
    monkeypatch.setattr(
        service_registry,
        "get_session",
        lambda: (_ for _ in ()).throw(RuntimeError("open failed")),
    )
    assert ExampleService()._load_settings() == {}

    query_failure = FakeSession(error=RuntimeError("query failed"))
    monkeypatch.setattr(service_registry, "get_session", lambda: query_failure)
    assert ExampleService()._load_settings() == {}
    assert query_failure.closed is True

    close_failure = FakeSession(
        [
            PluginSetting(
                plugin_name="example",
                key="retained",
                value="value",
                default_value=None,
            )
        ],
        close_error=RuntimeError("close failed"),
    )
    monkeypatch.setattr(service_registry, "get_session", lambda: close_failure)
    assert ExampleService()._load_settings() == {"retained": "value"}
    assert close_failure.closed is True


def test_registry_registers_actions_and_configures_task_manager(monkeypatch):
    actions = FakeActionRegistry()
    manager = FakeTaskManager()
    monkeypatch.setattr(service_registry, "action_registry", actions)
    registry = service_registry.ServiceRegistry()
    registry.set_task_manager(manager)
    service = ExampleService()

    registry.register(service)

    assert registry.list() == [service]
    assert registry.get("example") is service
    assert registry.get("missing") is None
    assert len(actions.registered) == 1
    definition, handler = actions.registered[0]
    assert definition.service == "example"
    assert handler.__self__ is service
    assert manager.configured == [("example", 3, "http://example.test")]


def test_registry_replaces_and_unregisters_services(monkeypatch):
    actions = FakeActionRegistry()
    manager = FakeTaskManager(remove_error=True)
    monkeypatch.setattr(service_registry, "action_registry", actions)
    registry = service_registry.ServiceRegistry()
    registry.set_task_manager(manager)
    first = ExampleService()
    second = ExampleService()

    registry.register(first)
    registry.register(second)
    registry.unregister("missing")
    registry.unregister("example")

    assert registry.list() == []
    assert actions.unregistered == ["example", "example"]


def test_registry_tracks_pending_configuration_and_retries(monkeypatch):
    actions = FakeActionRegistry()
    monkeypatch.setattr(service_registry, "action_registry", actions)
    registry = service_registry.ServiceRegistry()
    failing = FakeTaskManager(configure_error=True)
    registry.set_task_manager(failing)
    service = ExampleService()

    registry.register(service)
    assert registry._pending_task_configs == {"example"}

    working = FakeTaskManager()
    registry.set_task_manager(working)
    assert registry._pending_task_configs == set()
    assert working.configured == [("example", 3, "http://example.test")]


def test_registry_unregisters_all_services_owned_by_plugin(monkeypatch):
    actions = FakeActionRegistry()
    manager = FakeTaskManager()
    monkeypatch.setattr(service_registry, "action_registry", actions)
    registry = service_registry.ServiceRegistry()
    registry.set_task_manager(manager)

    first = ExampleService()
    first.name = "first"
    first.plugin_name = "shared-plugin"
    second = ExampleService()
    second.name = "second"
    second.plugin_name = "shared-plugin"
    other = ExampleService()
    other.name = "other"
    other.plugin_name = "other-plugin"
    registry.register(first)
    registry.register(second)
    registry.register(other)

    registry.unregister_by_plugin("shared-plugin")

    assert [service.name for service in registry.list()] == ["other"]
    assert manager.removed == ["first", "second"]


def test_pending_configuration_discards_missing_service():
    registry = service_registry.ServiceRegistry()
    registry._pending_task_configs.add("missing")
    registry._task_manager = FakeTaskManager()

    registry._configure_pending_services()

    assert registry._pending_task_configs == set()


def test_refresh_registered_services_reloads_and_reconfigures(monkeypatch):
    reloaded = []

    class Reloadable:
        name = "reloadable"

        def reload_settings(self):
            reloaded.append(self.name)

    class Broken:
        name = "broken"

        def reload_settings(self):
            raise RuntimeError("reload failed")

    configured = []
    fake_services = SimpleNamespace(
        list=lambda: [Reloadable(), Broken()],
        _configure_service=lambda service: configured.append(service.name),
    )
    monkeypatch.setattr(service_registry, "services", fake_services)

    service_registry._refresh_registered_services()

    assert reloaded == ["reloadable"]
    assert configured == ["reloadable"]
