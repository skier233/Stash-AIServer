"""Unit tests for plugin setting registration and loading."""

from stash_ai_server.models.plugin import PluginSetting
from stash_ai_server.plugin_runtime import settings_registry


class FakeScalars:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return list(self.rows)


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return FakeScalars(self.rows)


class FakeSession:
    def __init__(self, rows=(), *, execute_error: Exception | None = None):
        self.rows = list(rows)
        self.execute_error = execute_error
        self.added = []
        self.commits = 0
        self.closed = False

    def execute(self, _statement):
        if self.execute_error is not None:
            raise self.execute_error
        return FakeResult(self.rows)

    def add(self, row):
        self.added.append(row)
        self.rows.append(row)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


def _setting(**overrides):
    values = {
        "plugin_name": "example",
        "key": "mode",
        "type": "string",
        "label": "Mode",
        "description": "Original description",
        "default_value": "old",
        "options": ["old"],
        "value": "custom",
    }
    values.update(overrides)
    return PluginSetting(**values)


def test_register_settings_adds_normalized_definitions_and_skips_missing_keys():
    session = FakeSession()

    settings_registry.register_settings(
        session,
        "example",
        [
            {
                "name": "mode",
                "type": "select",
                "label": "Mode",
                "help": "Select a mode",
                "default": "null",
                "options": ["one", "null"],
            },
            {"label": "Missing key"},
        ],
    )

    assert session.commits == 1
    assert len(session.added) == 1
    row = session.added[0]
    assert row.plugin_name == "example"
    assert row.key == "mode"
    assert row.type == "select"
    assert row.label == "Mode"
    assert row.description == "Select a mode"
    assert row.default_value is None
    assert row.options == ["one", None]
    assert row.value is None


def test_register_settings_updates_metadata_without_overwriting_custom_value():
    row = _setting()
    session = FakeSession([row])

    settings_registry.register_settings(
        session,
        "example",
        [
            {
                "key": "mode",
                "type": "select",
                "label": "New mode",
                "description": "New description",
                "default": "new",
                "options": ["new", "other"],
            }
        ],
    )

    assert session.commits == 1
    assert row.type == "select"
    assert row.label == "New mode"
    assert row.description == "New description"
    assert row.default_value == "new"
    assert row.options == ["new", "other"]
    assert row.value == "custom"


def test_register_settings_populates_missing_value_and_avoids_noop_commit():
    row = _setting(default_value="default", value=None)
    session = FakeSession([row])

    settings_registry.register_settings(
        session,
        "example",
        [
            {
                "key": "mode",
                "type": "string",
                "label": "Mode",
                "description": "Original description",
                "default": "default",
                "options": ["old"],
            }
        ],
    )
    assert row.value == "default"
    assert session.commits == 1

    settings_registry.register_settings(
        session,
        "example",
        [
            {
                "key": "mode",
                "type": "string",
                "label": "Mode",
                "description": "Original description",
                "default": "default",
                "options": ["old"],
            }
        ],
    )
    assert session.commits == 1


def test_load_plugin_settings_resolves_values_and_closes_session(monkeypatch):
    rows = [
        _setting(key="custom", value="value", default_value="default"),
        _setting(key="fallback", value=None, default_value=42),
        _setting(key="empty", value=None, default_value=None),
    ]
    session = FakeSession(rows)
    monkeypatch.setattr(
        "stash_ai_server.db.session.get_session_local",
        lambda: lambda: session,
    )

    assert settings_registry.load_plugin_settings("example") == {
        "custom": "value",
        "fallback": 42,
    }
    assert session.closed is True


def test_load_plugin_settings_handles_factory_and_query_failures(monkeypatch):
    monkeypatch.setattr(
        "stash_ai_server.db.session.get_session_local",
        lambda: (_ for _ in ()).throw(RuntimeError("factory failed")),
    )
    assert settings_registry.load_plugin_settings("example") == {}

    session = FakeSession(execute_error=RuntimeError("query failed"))
    monkeypatch.setattr(
        "stash_ai_server.db.session.get_session_local",
        lambda: lambda: session,
    )
    assert settings_registry.load_plugin_settings("example") == {}
    assert session.closed is True


def test_load_plugin_settings_ignores_close_failures(monkeypatch):
    session = FakeSession([_setting()])

    def fail_close():
        raise RuntimeError("close failed")

    session.close = fail_close
    monkeypatch.setattr(
        "stash_ai_server.db.session.get_session_local",
        lambda: lambda: session,
    )

    assert settings_registry.load_plugin_settings("example") == {"mode": "custom"}
