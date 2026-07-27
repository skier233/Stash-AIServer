"""Unit tests for database-backed system settings."""

import sys

import pytest

from stash_ai_server.core import system_settings
from stash_ai_server.models.plugin import PluginSetting


class FakeResult:
    def __init__(self, rows=(), scalar=None):
        self.rows = list(rows)
        self.scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)

    def scalar_one_or_none(self):
        if self.scalar is not None:
            return self.scalar
        return self.rows[0] if self.rows else None


class FakeSession:
    def __init__(self, rows=(), *, connection_error=None):
        self.rows = list(rows)
        self.connection_error = connection_error
        self.added = []
        self.commits = 0
        self.close_calls = 0
        self.executed = []

    def execute(self, statement):
        statement_text = str(statement)
        self.executed.append(statement_text)
        if statement_text.strip() == "SELECT 1":
            if self.connection_error is not None:
                raise self.connection_error
            return FakeResult()
        return FakeResult(self.rows)

    def add(self, row):
        self.added.append(row)
        self.rows.append(row)

    def commit(self):
        self.commits += 1

    def close(self):
        self.close_calls += 1


@pytest.fixture(autouse=True)
def reset_system_settings(monkeypatch):
    original_cache = dict(system_settings._CACHE)
    monkeypatch.setattr(system_settings, "_session_factory", None)
    monkeypatch.setattr(system_settings, "_CACHE_LOADED", False)
    system_settings._CACHE.clear()
    try:
        yield
    finally:
        system_settings._CACHE.clear()
        system_settings._CACHE.update(original_cache)


@pytest.mark.parametrize(
    ("setting_type", "value", "expected"),
    [
        ("string", None, None),
        ("string", "value", "value"),
        ("number", "2.5", 2.5),
        ("number", "invalid", "invalid"),
        ("boolean", True, True),
        ("boolean", 0, False),
        ("boolean", 2, True),
        ("boolean", "YES", True),
        ("boolean", "off", False),
        ("boolean", object(), False),
    ],
)
def test_coerce_value_handles_supported_types(setting_type, value, expected):
    actual = system_settings._coerce_value(setting_type, value)
    if setting_type == "boolean":
        assert actual is expected
    else:
        assert actual == expected


def test_session_factory_override_and_default_session(monkeypatch):
    custom_session = object()
    system_settings.set_session_factory(lambda: custom_session)
    assert system_settings._get_session() is custom_session

    default_session = object()
    monkeypatch.setattr(system_settings, "_session_factory", None)
    monkeypatch.setattr(
        "stash_ai_server.db.session.get_session",
        lambda: default_session,
    )
    assert system_settings._get_session() is default_session


def test_seed_system_settings_creates_rows_from_defaults_and_environment(
    monkeypatch,
):
    definitions = [
        {
            "key": "FEATURE_ENABLED",
            "type": "boolean",
            "label": "Feature enabled",
            "description": "Toggle",
            "default": False,
        },
        {
            "key": "RETRY_COUNT",
            "type": "number",
            "label": "Retry count",
            "default": 3,
            "options": ["null", 5],
        },
    ]
    session = FakeSession()
    monkeypatch.setattr(system_settings, "_DEFS", definitions)
    monkeypatch.setenv("FEATURE_ENABLED", "true")
    monkeypatch.delenv("RETRY_COUNT", raising=False)
    system_settings.set_session_factory(lambda: session)
    system_settings._CACHE["stale"] = "value"
    system_settings._CACHE_LOADED = True

    system_settings.seed_system_settings()

    assert session.commits == 1
    assert session.close_calls == 1
    assert [row.key for row in session.added] == [
        "FEATURE_ENABLED",
        "RETRY_COUNT",
    ]
    assert session.added[0].value is True
    assert session.added[1].value == 3
    assert session.added[1].options == [None, 5]
    assert system_settings._CACHE == {}
    assert system_settings._CACHE_LOADED is False


def test_seed_system_settings_updates_metadata_and_avoids_noop_commit(
    monkeypatch,
):
    definition = {
        "key": "RETRY_COUNT",
        "type": "number",
        "label": "Retry count",
        "description": "Current description",
        "default": 3,
        "options": [1, 3],
    }
    existing = PluginSetting(
        plugin_name=system_settings.SYSTEM_PLUGIN_NAME,
        key="RETRY_COUNT",
        type="string",
        label="Old label",
        description="Old description",
        default_value=3,
        options=None,
        value=3,
    )
    session = FakeSession([existing])
    monkeypatch.setattr(system_settings, "_DEFS", [definition])
    monkeypatch.setenv("RETRY_COUNT", "7")
    system_settings.set_session_factory(lambda: session)

    system_settings.seed_system_settings()

    assert existing.type == "number"
    assert existing.label == "Retry count"
    assert existing.description == "Current description"
    assert existing.options == [1, 3]
    assert existing.value == 7.0
    assert session.commits == 1

    monkeypatch.delenv("RETRY_COUNT")
    system_settings.seed_system_settings()
    assert session.commits == 1


def test_seed_system_settings_skips_when_database_is_unavailable(monkeypatch):
    session = FakeSession(connection_error=RuntimeError("offline"))
    monkeypatch.setattr(system_settings, "_DEFS", [])
    system_settings.set_session_factory(lambda: session)

    system_settings.seed_system_settings()

    assert session.added == []
    assert session.commits == 0
    assert session.close_calls == 2


def test_cache_loading_uses_explicit_values_and_defaults():
    explicit = PluginSetting(
        plugin_name=system_settings.SYSTEM_PLUGIN_NAME,
        key="EXPLICIT",
        type="string",
        value="chosen",
        default_value="default",
    )
    fallback = PluginSetting(
        plugin_name=system_settings.SYSTEM_PLUGIN_NAME,
        key="FALLBACK",
        type="string",
        value=None,
        default_value="default",
    )
    session = FakeSession([explicit, fallback])

    system_settings._ensure_cache(session)
    session.rows = []
    system_settings._ensure_cache(session)

    assert system_settings._CACHE == {
        "EXPLICIT": "chosen",
        "FALLBACK": "default",
    }
    assert len(session.executed) == 1


def test_get_value_uses_environment_in_test_mode(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "active")
    monkeypatch.setenv("TASK_LOOP_INTERVAL", "1.25")
    monkeypatch.setenv("TASK_DEBUG", "yes")
    monkeypatch.setenv("CUSTOM_SETTING", "custom")

    assert system_settings.get_value("TASK_LOOP_INTERVAL") == 1.25
    assert system_settings.get_value("TASK_DEBUG") is True
    assert system_settings.get_value("CUSTOM_SETTING") == "custom"
    assert system_settings.get_value("MISSING", "fallback") == "fallback"


def _disable_test_detection(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("_", "python")
    monkeypatch.setattr(sys, "argv", ["stash-ai-server"])


def test_get_value_loads_database_cache_outside_test_mode(monkeypatch):
    _disable_test_detection(monkeypatch)
    row = PluginSetting(
        plugin_name=system_settings.SYSTEM_PLUGIN_NAME,
        key="SETTING",
        type="string",
        value="database",
        default_value="default",
    )
    session = FakeSession([row])
    system_settings.set_session_factory(lambda: session)

    assert system_settings.get_value("SETTING", "fallback") == "database"
    assert system_settings.get_value("MISSING", "fallback") == "fallback"
    assert session.close_calls == 2


def test_get_value_returns_default_for_database_failures(monkeypatch):
    _disable_test_detection(monkeypatch)
    unavailable = FakeSession(connection_error=RuntimeError("offline"))
    system_settings.set_session_factory(lambda: unavailable)

    assert system_settings.get_value("SETTING", "fallback") == "fallback"
    assert unavailable.close_calls == 1

    system_settings.set_session_factory(
        lambda: (_ for _ in ()).throw(RuntimeError("open failed"))
    )
    assert system_settings.get_value("SETTING", "fallback") == "fallback"


def test_set_value_updates_existing_row_and_cache():
    row = PluginSetting(
        plugin_name=system_settings.SYSTEM_PLUGIN_NAME,
        key="TASK_DEBUG",
        type="boolean",
        value=False,
    )
    session = FakeSession([row])
    system_settings.set_session_factory(lambda: session)

    system_settings.set_value("TASK_DEBUG", "true")

    assert row.value is True
    assert session.added == []
    assert session.commits == 1
    assert session.close_calls == 1
    assert system_settings._CACHE["TASK_DEBUG"] is True


def test_set_value_creates_unknown_setting_and_invalidates_cache():
    session = FakeSession()
    system_settings.set_session_factory(lambda: session)

    system_settings.set_value("CUSTOM_SETTING", 42)
    system_settings._CACHE_LOADED = True
    system_settings.invalidate_cache()

    [created] = session.added
    assert created.plugin_name == system_settings.SYSTEM_PLUGIN_NAME
    assert created.key == "CUSTOM_SETTING"
    assert created.type == "string"
    assert created.label == "CUSTOM_SETTING"
    assert created.value == 42
    assert session.commits == 1
    assert system_settings._CACHE["CUSTOM_SETTING"] == 42
    assert system_settings._CACHE_LOADED is False
