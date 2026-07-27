"""Unit tests for system health probes and status aggregation."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from stash_ai_server.api import system as system_api
from stash_ai_server.schemas.health import HealthComponent, HealthStatus


class FakeStashInterface:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def find_tags(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return []


class FakeDatabaseSession:
    def __init__(self, error=None):
        self.error = error
        self.executed = []

    def execute(self, statement):
        self.executed.append(str(statement))
        if self.error is not None:
            raise self.error


def _stash_client(*, url="", api_key="", interface=None):
    return SimpleNamespace(
        stash_url=url,
        _effective_url=url or None,
        api_key=api_key,
        stash_interface=interface,
    )


@pytest.mark.asyncio
async def test_stash_api_probe_warns_when_url_is_missing(monkeypatch):
    monkeypatch.setattr(
        system_api,
        "stash_api",
        _stash_client(api_key="secret"),
    )

    component = await system_api._probe_stash_api()

    assert component.status == HealthStatus.WARN
    assert component.message == "Stash URL not configured"
    assert component.details == {
        "configured_url": None,
        "effective_url": None,
        "api_key_configured": True,
    }


@pytest.mark.asyncio
async def test_stash_api_probe_errors_when_client_cannot_initialize(monkeypatch):
    monkeypatch.setattr(
        system_api,
        "stash_api",
        _stash_client(url="http://stash.test"),
    )

    component = await system_api._probe_stash_api()

    assert component.status == HealthStatus.ERROR
    assert component.message == "Unable to initialize Stash API client"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("api_key", "expected_status", "expected_message"),
    [
        ("secret", HealthStatus.OK, "Connected to Stash API"),
        (
            "",
            HealthStatus.WARN,
            "Connected to Stash API (API key not configured)",
        ),
    ],
)
async def test_stash_api_probe_reports_success_and_missing_key_warning(
    monkeypatch,
    api_key,
    expected_status,
    expected_message,
):
    interface = FakeStashInterface()
    monkeypatch.setattr(
        system_api,
        "stash_api",
        _stash_client(
            url="http://stash.test",
            api_key=api_key,
            interface=interface,
        ),
    )

    component = await system_api._probe_stash_api()

    assert component.status == expected_status
    assert component.message == expected_message
    assert interface.calls == [
        {"filter": {"per_page": 1, "page": 1}, "fragment": "id"}
    ]


@pytest.mark.asyncio
async def test_stash_api_probe_reports_request_failure(monkeypatch):
    monkeypatch.setattr(
        system_api,
        "stash_api",
        _stash_client(
            url="http://stash.test",
            api_key="secret",
            interface=FakeStashInterface(RuntimeError("offline")),
        ),
    )

    component = await system_api._probe_stash_api()

    assert component.status == HealthStatus.ERROR
    assert component.message == "Failed to reach Stash API"
    assert component.details["last_error"] == "offline"


@pytest.mark.asyncio
async def test_stash_database_probe_warns_when_path_is_missing(monkeypatch):
    monkeypatch.setattr(system_api, "sys_get", lambda _key: None)
    monkeypatch.setattr(
        system_api,
        "stash_db",
        SimpleNamespace(get_stash_sessionmaker=lambda: None),
    )

    component = await system_api._probe_stash_database()

    assert component.status == HealthStatus.WARN
    assert component.message == "Stash database path not configured"
    assert component.details["configured_path"] is None
    assert component.details["last_error"] == (
        "Stash database is not configured or unavailable"
    )


@pytest.mark.asyncio
async def test_stash_database_probe_reports_missing_file(monkeypatch, tmp_path):
    missing_path = tmp_path / "missing.sqlite"
    monkeypatch.setattr(system_api, "sys_get", lambda _key: str(missing_path))
    monkeypatch.setattr(
        system_api,
        "mutate_path_for_backend",
        lambda path: path,
    )
    monkeypatch.setattr(
        system_api,
        "stash_db",
        SimpleNamespace(get_stash_sessionmaker=lambda: None),
    )

    component = await system_api._probe_stash_database()

    assert component.status == HealthStatus.ERROR
    assert component.message == "Configured database path was not found"
    assert component.details["path_exists"] is False


@pytest.mark.asyncio
async def test_stash_database_probe_reports_accessible_database(
    monkeypatch,
    tmp_path,
):
    database_path = tmp_path / "stash.sqlite"
    database_path.touch()
    session = FakeDatabaseSession()
    monkeypatch.setattr(system_api, "sys_get", lambda _key: str(database_path))
    monkeypatch.setattr(
        system_api,
        "mutate_path_for_backend",
        lambda path: path,
    )
    monkeypatch.setattr(
        system_api,
        "stash_db",
        SimpleNamespace(
            get_stash_sessionmaker=lambda: lambda: nullcontext(session)
        ),
    )

    component = await system_api._probe_stash_database()

    assert component.status == HealthStatus.OK
    assert component.message == "Stash database accessible"
    assert component.details["path_exists"] is True
    assert session.executed == ["SELECT 1"]


@pytest.mark.asyncio
async def test_stash_database_probe_reports_open_and_mutation_failures(
    monkeypatch,
    tmp_path,
):
    database_path = tmp_path / "stash.sqlite"
    database_path.touch()
    monkeypatch.setattr(system_api, "sys_get", lambda _key: str(database_path))
    monkeypatch.setattr(
        system_api,
        "mutate_path_for_backend",
        lambda _path: (_ for _ in ()).throw(ValueError("mapping failed")),
    )
    monkeypatch.setattr(
        system_api,
        "stash_db",
        SimpleNamespace(
            get_stash_sessionmaker=lambda: lambda: nullcontext(
                FakeDatabaseSession(RuntimeError("database offline"))
            )
        ),
    )

    component = await system_api._probe_stash_database()

    assert component.status == HealthStatus.ERROR
    assert component.message == "Failed to open Stash database"
    assert component.details["mutation_error"] == "mapping failed"
    assert component.details["last_error"] == "database offline"


@pytest.mark.asyncio
async def test_system_health_uses_worst_component_and_version_payload(
    monkeypatch,
):
    stash_component = HealthComponent(
        status=HealthStatus.WARN,
        message="stash warning",
    )
    database_component = HealthComponent(
        status=HealthStatus.ERROR,
        message="database error",
    )

    async def probe_stash():
        return stash_component

    async def probe_database():
        return database_component

    monkeypatch.setattr(system_api, "_probe_stash_api", probe_stash)
    monkeypatch.setattr(system_api, "_probe_stash_database", probe_database)
    monkeypatch.setattr(
        system_api,
        "get_version_payload",
        lambda db: {
            "version": "1.2.3",
            "db_alembic_head": "revision",
            "frontend_min_version": "1.0.0",
        },
    )
    db = object()

    snapshot = await system_api.get_system_health(db)

    assert snapshot.status == HealthStatus.ERROR
    assert snapshot.stash_api is stash_component
    assert snapshot.database is database_component
    assert snapshot.backend_version == "1.2.3"
    assert snapshot.db_alembic_head == "revision"
    assert snapshot.version_payload["frontend_min_version"] == "1.0.0"
