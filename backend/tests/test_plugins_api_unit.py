from __future__ import annotations

from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

from stash_ai_server.api import plugins
from stash_ai_server.models.plugin import PluginCatalog
from stash_ai_server.models.plugin import PluginMeta
from stash_ai_server.models.plugin import PluginSetting
from stash_ai_server.models.plugin import PluginSource


@pytest.fixture
def plugin_api_db(tmp_path):
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'plugin-api.sqlite'}")
    for table in (
        PluginSource.__table__,
        PluginCatalog.__table__,
        PluginMeta.__table__,
        PluginSetting.__table__,
    ):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def make_plan(plugin="target", **overrides):
    values = {
        "plugin": plugin,
        "order": [plugin],
        "dependencies": [],
        "already_active": [],
        "missing": [],
        "catalog_rows": {plugin: object()},
        "human_names": {plugin: "Target"},
        "required_backend": {},
    }
    values.update(overrides)
    return plugins.plugin_loader.InstallPlanResult(**values)


def add_source(db, name="source", enabled=True):
    source = PluginSource(
        name=name,
        url=f"https://{name}.example",
        enabled=enabled,
    )
    db.add(source)
    db.commit()
    return source


def add_meta(db, name="target", status="active", version="1"):
    meta = PluginMeta(
        name=name,
        version=version,
        required_backend=">=0",
        status=status,
    )
    db.add(meta)
    db.commit()
    return meta


def test_require_plugin_active_accepts_active_and_reports_other_states(plugin_api_db):
    with plugin_api_db() as db:
        active = add_meta(db)
        assert plugins._require_plugin_active(db, "target") is active

        active.status = "error"
        active.last_error = "activation failed"
        db.commit()
        with pytest.raises(HTTPException) as inactive:
            plugins._require_plugin_active(db, "target")
        assert inactive.value.status_code == 409
        assert inactive.value.detail["status"] == "error"
        assert inactive.value.detail["message"] == "activation failed"

        with pytest.raises(HTTPException) as missing:
            plugins._require_plugin_active(db, "missing")
        assert missing.value.detail["status"] == "missing"


def test_require_backend_compatibility_skips_empty_and_rejects_mismatch(monkeypatch):
    plan = make_plan(
        order=["dependency", "target"],
        required_backend={"dependency": None, "target": ">=99"},
    )
    monkeypatch.setattr(plugins, "version_satisfies", lambda version, required: False)
    with pytest.raises(HTTPException) as raised:
        plugins._require_backend_compatibility(plan)
    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "BACKEND_TOO_OLD"
    assert raised.value.detail["plugin"] == "target"

    monkeypatch.setattr(plugins, "version_satisfies", lambda version, required: True)
    plugins._require_backend_compatibility(plan)


@pytest.mark.asyncio
async def test_list_installed_filters_active_and_removed_rows(plugin_api_db):
    with plugin_api_db() as db:
        add_meta(db, "active", "active")
        add_meta(db, "error", "error")
        add_meta(db, "removed", "removed")

        assert {
            row.name for row in await plugins.list_installed(db=db)
        } == {"active", "error"}
        assert [
            row.name for row in await plugins.list_installed(active_only=True, db=db)
        ] == ["active"]
        assert {
            row.name
            for row in await plugins.list_installed(include_removed=True, db=db)
        } == {"active", "error", "removed"}


@pytest.mark.asyncio
async def test_source_endpoints_are_idempotent_and_protect_local(plugin_api_db):
    with plugin_api_db() as db:
        local = add_source(db, "local")
        remote = add_source(db, "remote")
        assert [row.name for row in await plugins.list_sources(db)] == ["remote"]

        payload = plugins.PluginSourceCreate(
            name="remote", url="https://changed.example", enabled=False
        )
        assert await plugins.create_source(payload, db) is remote
        created = await plugins.create_source(
            plugins.PluginSourceCreate(name="new", url="https://new.example"), db
        )
        assert created.id is not None

        with pytest.raises(HTTPException) as missing:
            await plugins.delete_source("missing", db)
        assert missing.value.status_code == 404
        with pytest.raises(HTTPException) as immutable:
            await plugins.delete_source("local", db)
        assert immutable.value.detail == "SOURCE_IMMUTABLE"
        assert await plugins.delete_source("remote", db) == {"status": "deleted"}


@pytest.mark.asyncio
async def test_plugin_setting_list_and_upsert_cover_types_defaults_and_cache(
    plugin_api_db, monkeypatch
):
    invalidations = []
    monkeypatch.setattr(
        plugins,
        "invalidate_path_mapping_cache",
        lambda plugin_name=None, **kwargs: invalidations.append(
            (plugin_name, kwargs)
        ),
    )
    with plugin_api_db() as db:
        db.add_all(
            [
                PluginSetting(
                    plugin_name="plugin",
                    key="number",
                    label=None,
                    type="number",
                    default_value=3,
                ),
                PluginSetting(
                    plugin_name="plugin",
                    key="boolean",
                    type="boolean",
                    default_value=False,
                ),
                PluginSetting(
                    plugin_name="plugin",
                    key="select",
                    type="select",
                    options=["one", "two"],
                    default_value="one",
                ),
                PluginSetting(
                    plugin_name="plugin",
                    key="path_mappings",
                    type="string",
                    default_value=[],
                ),
            ]
        )
        db.commit()

        listed = await plugins.list_plugin_settings("plugin", db)
        assert listed[0].label == "number"
        assert listed[0].value == 3

        assert await plugins.upsert_setting(
            "plugin", "number", plugins.SettingUpsert(value="4.5"), db
        ) == {"status": "ok"}
        assert await plugins.upsert_setting(
            "plugin", "boolean", plugins.SettingUpsert(value="true"), db
        ) == {"status": "ok"}
        assert await plugins.upsert_setting(
            "plugin", "boolean", plugins.SettingUpsert(value=0), db
        ) == {"status": "ok"}
        assert await plugins.upsert_setting(
            "plugin", "select", plugins.SettingUpsert(value="two"), db
        ) == {"status": "ok"}
        assert await plugins.upsert_setting(
            "plugin", "path_mappings", plugins.SettingUpsert(value=[]), db
        ) == {"status": "ok"}
        assert invalidations == [("plugin", {})]

        assert await plugins.upsert_setting(
            "plugin", "new", plugins.SettingUpsert(value="value"), db
        ) == {"status": "ok"}
        created = db.scalar(
            sa.select(PluginSetting).where(
                PluginSetting.plugin_name == "plugin",
                PluginSetting.key == "new",
            )
        )
        assert created.label == "new"

        for key, value, detail in (
            ("number", "bad", "INVALID_NUMBER"),
            ("boolean", "maybe", "INVALID_BOOLEAN"),
            ("select", "missing", "INVALID_OPTION"),
        ):
            with pytest.raises(HTTPException) as raised:
                await plugins.upsert_setting(
                    "plugin", key, plugins.SettingUpsert(value=value), db
                )
            assert raised.value.detail == detail


@pytest.mark.asyncio
async def test_system_setting_endpoints_trigger_precise_side_effects(
    plugin_api_db, monkeypatch
):
    cache_calls = []
    path_calls = []
    engine_calls = []
    restart_calls = []
    monkeypatch.setattr(
        plugins, "sys_invalidate_cache", lambda: cache_calls.append("cache")
    )
    monkeypatch.setattr(
        plugins,
        "invalidate_path_mapping_cache",
        lambda *args, **kwargs: path_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        plugins.stash_db,
        "get_stash_engine",
        lambda **kwargs: engine_calls.append(kwargs) or object(),
    )
    monkeypatch.setattr(
        plugins, "schedule_backend_restart", lambda: restart_calls.append("restart")
    )

    with plugin_api_db() as db:
        db.add_all(
            [
                PluginSetting(
                    plugin_name=plugins.SYSTEM_PLUGIN_NAME,
                    key="PATH_MAPPINGS",
                    type="string",
                    default_value=[],
                ),
                PluginSetting(
                    plugin_name=plugins.SYSTEM_PLUGIN_NAME,
                    key="STASH_URL",
                    type="string",
                    default_value="http://old",
                ),
                PluginSetting(
                    plugin_name=plugins.SYSTEM_PLUGIN_NAME,
                    key="FLAG",
                    type="boolean",
                    default_value=False,
                ),
                PluginSetting(
                    plugin_name=plugins.SYSTEM_PLUGIN_NAME,
                    key="LIMIT",
                    type="number",
                    default_value=1,
                ),
                PluginSetting(
                    plugin_name=plugins.SYSTEM_PLUGIN_NAME,
                    key="MODE",
                    type="select",
                    options=["one", "two"],
                    default_value="one",
                ),
            ]
        )
        db.commit()

        listed = await plugins.list_system_settings(db)
        assert [setting.key for setting in listed] == [
            "PATH_MAPPINGS",
            "STASH_URL",
            "FLAG",
            "LIMIT",
            "MODE",
        ]
        await plugins.upsert_system_setting(
            "PATH_MAPPINGS", plugins.SettingUpsert(value=[{"from": "a"}]), db
        )
        await plugins.upsert_system_setting(
            "STASH_URL", plugins.SettingUpsert(value="http://new"), db
        )
        await plugins.upsert_system_setting(
            "FLAG", plugins.SettingUpsert(value=1), db
        )
        await plugins.upsert_system_setting(
            "LIMIT", plugins.SettingUpsert(value="2.5"), db
        )
        await plugins.upsert_system_setting(
            "MODE", plugins.SettingUpsert(value="two"), db
        )

        assert len(cache_calls) == 5
        assert path_calls == [((), {"system": True})]
        assert engine_calls == [{"refresh": True}]
        assert restart_calls == ["restart"]

        with pytest.raises(HTTPException) as missing:
            await plugins.upsert_system_setting(
                "MISSING", plugins.SettingUpsert(value=1), db
            )
        assert missing.value.status_code == 404

        for key, value, detail in (
            ("LIMIT", "bad", "INVALID_NUMBER"),
            ("FLAG", "bad", "INVALID_BOOLEAN"),
            ("MODE", "bad", "INVALID_OPTION"),
        ):
            with pytest.raises(HTTPException) as raised:
                await plugins.upsert_system_setting(
                    key, plugins.SettingUpsert(value=value), db
                )
            assert raised.value.detail == detail


class Response:
    def __init__(self, status_code=200, payload=None, json_error=None):
        self.status_code = status_code
        self.payload = payload
        self.json_error = json_error

    def json(self):
        if self.json_error is not None:
            raise self.json_error
        return self.payload


@pytest.fixture
def async_client_double(monkeypatch):
    state = SimpleNamespace(response=Response(), error=None)

    class Client:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def get(self, url):
            if state.error is not None:
                raise state.error
            return state.response

    monkeypatch.setattr(
        plugins,
        "httpx",
        SimpleNamespace(AsyncClient=Client),
    )
    return state


@pytest.mark.asyncio
async def test_refresh_source_validates_source_state(
    plugin_api_db, async_client_double
):
    with plugin_api_db() as db:
        with pytest.raises(HTTPException) as missing:
            await plugins.refresh_source("missing", db)
        assert missing.value.status_code == 404

        add_source(db, "disabled", enabled=False)
        with pytest.raises(HTTPException) as disabled:
            await plugins.refresh_source("disabled", db)
        assert disabled.value.detail == "SOURCE_DISABLED"

        add_source(db, "local")
        with pytest.raises(HTTPException) as immutable:
            await plugins.refresh_source("local", db)
        assert immutable.value.detail == "SOURCE_IMMUTABLE"


@pytest.mark.asyncio
async def test_refresh_source_imports_catalog_and_normalizes_dependencies(
    plugin_api_db, async_client_double
):
    async_client_double.response = Response(
        payload={
            "schemaVersion": 1,
            "plugins": [
                {
                    "name": "one",
                    "version": "2",
                    "dependsOn": [" dep ", "null", None],
                    "humanName": "One",
                    "serverLink": "https://one.example",
                    "optional": "null",
                },
                {
                    "plugin_name": "two",
                    "depends_on": "dep-two",
                    "description": "Two",
                },
                {"version": "missing-name"},
            ],
        }
    )
    with plugin_api_db() as db:
        source = add_source(db)
        db.add(
            PluginCatalog(
                source_id=source.id, plugin_name="old", version="1"
            )
        )
        db.commit()

        result = await plugins.refresh_source("source", db)

        assert result.fetched == 2
        assert result.errors == []
        rows = db.scalars(
            sa.select(PluginCatalog).order_by(PluginCatalog.plugin_name)
        ).all()
        assert [row.plugin_name for row in rows] == ["one", "two"]
        assert rows[0].dependencies_json == {"plugins": ["dep"]}
        assert rows[0].manifest_json["optional"] is None
        assert rows[1].dependencies_json == {"plugins": ["dep-two"]}
        assert source.last_error is None
        assert source.last_refreshed_at is not None


@pytest.mark.asyncio
async def test_refresh_source_reports_http_json_schema_and_transport_errors(
    plugin_api_db, async_client_double
):
    with plugin_api_db() as db:
        source = add_source(db)

        async_client_double.response = Response(status_code=503)
        assert (await plugins.refresh_source("source", db)).errors == ["HTTP 503"]

        async_client_double.response = Response(json_error=ValueError("bad json"))
        assert (
            await plugins.refresh_source("source", db)
        ).errors[0].startswith("json_parse_error:")

        async_client_double.response = Response(
            payload={"schemaVersion": 9, "plugins": []}
        )
        assert "schema_version_mismatch" in (
            await plugins.refresh_source("source", db)
        ).errors[0]

        async_client_double.error = RuntimeError("network failed")
        result = await plugins.refresh_source("source", db)
        assert result.errors == ["exception:network failed"]
        assert source.last_error == "network failed"


@pytest.mark.asyncio
async def test_catalog_and_install_plan_endpoints(plugin_api_db, monkeypatch):
    with plugin_api_db() as db:
        with pytest.raises(HTTPException):
            await plugins.list_catalog("missing", db)
        source = add_source(db)
        db.add(
            PluginCatalog(
                source_id=source.id,
                plugin_name="target",
                version="2",
                description="Description",
                manifest_json={"path": "target"},
            )
        )
        db.commit()
        assert await plugins.list_catalog("source", db) == [
            {
                "plugin_name": "target",
                "version": "2",
                "description": "Description",
                "manifest": {"path": "target"},
            }
        ]

        with pytest.raises(HTTPException) as required:
            await plugins.install_plan({}, db)
        assert required.value.detail == "PLUGIN_REQUIRED"

        with pytest.raises(HTTPException) as missing_source:
            await plugins.install_plan(
                {"plugin": "target", "source": "missing"}, db
            )
        assert missing_source.value.detail == "SOURCE_NOT_FOUND"

        plan = make_plan(
            order=["dependency", "target"],
            dependencies=["dependency"],
            already_active=["dependency"],
            missing=[],
            human_names={"dependency": "Dependency", "target": "Target"},
        )
        monkeypatch.setattr(plugins.plugin_loader, "plan_install", lambda *args, **kwargs: plan)
        response = await plugins.install_plan(
            {"plugin": "target", "source": "source"}, db
        )
        assert response.install_order == ["dependency", "target"]
        assert response.already_installed == ["dependency"]

        monkeypatch.setattr(
            plugins.plugin_loader,
            "plan_install",
            lambda *args, **kwargs: make_plan(catalog_rows={}),
        )
        with pytest.raises(HTTPException) as missing_plugin:
            await plugins.install_plan({"plugin": "target"}, db)
        assert missing_plugin.value.detail == "PLUGIN_NOT_FOUND"


@pytest.mark.asyncio
async def test_install_plugin_validates_dependencies_compatibility_and_execution(
    plugin_api_db, monkeypatch
):
    with plugin_api_db() as db:
        source = add_source(db)
        add_meta(db, version="9")

        with pytest.raises(HTTPException) as missing_source:
            await plugins.install_plugin(
                {"source": "missing", "plugin": "target"}, db
            )
        assert missing_source.value.status_code == 404

        plan = make_plan(missing=["absent"])
        monkeypatch.setattr(plugins.plugin_loader, "plan_install", lambda *args, **kwargs: plan)
        with pytest.raises(HTTPException) as missing_dependency:
            await plugins.install_plugin(
                {"source": "source", "plugin": "target"}, db
            )
        assert missing_dependency.value.detail["code"] == "DEPENDENCY_MISSING"

        plan = make_plan(
            order=["dependency", "target"],
            dependencies=["dependency"],
            human_names={"dependency": "Dependency"},
        )
        monkeypatch.setattr(plugins.plugin_loader, "plan_install", lambda *args, **kwargs: plan)
        monkeypatch.setattr(plugins, "_require_backend_compatibility", lambda plan: None)
        with pytest.raises(HTTPException) as required:
            await plugins.install_plugin(
                {"source": "source", "plugin": "target"}, db
            )
        assert required.value.detail["code"] == "DEPENDENCIES_REQUIRED"

        monkeypatch.setattr(
            plugins.plugin_loader,
            "execute_install_plan",
            lambda *args, **kwargs: [("dependency", "1"), ("target", "2")],
        )
        result = await plugins.install_plugin(
            {
                "source": "source",
                "plugin": "target",
                "overwrite": True,
                "install_dependencies": True,
            },
            db,
        )
        assert result["status"] == "installed"
        assert result["version"] == "2"

        monkeypatch.setattr(
            plugins.plugin_loader,
            "execute_install_plan",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("failed")),
        )
        with pytest.raises(HTTPException) as failed:
            await plugins.install_plugin(
                {
                    "source": "source",
                    "plugin": "target",
                    "install_dependencies": True,
                },
                db,
            )
        assert failed.value.status_code == 500


@pytest.mark.asyncio
async def test_update_plugin_success_and_failure_paths(plugin_api_db, monkeypatch):
    with plugin_api_db() as db:
        add_source(db)
        add_meta(db, version="9")

        with pytest.raises(HTTPException):
            await plugins.update_plugin(
                {"source": "missing", "plugin": "target"}, db
            )

        monkeypatch.setattr(
            plugins.plugin_loader,
            "plan_install",
            lambda *args, **kwargs: make_plan(catalog_rows={}),
        )
        with pytest.raises(HTTPException) as missing:
            await plugins.update_plugin(
                {"source": "source", "plugin": "target"}, db
            )
        assert missing.value.detail == "PLUGIN_NOT_FOUND"

        monkeypatch.setattr(
            plugins.plugin_loader,
            "plan_install",
            lambda *args, **kwargs: make_plan(missing=["dep"]),
        )
        with pytest.raises(HTTPException) as dependency:
            await plugins.update_plugin(
                {"source": "source", "plugin": "target"}, db
            )
        assert dependency.value.detail["code"] == "DEPENDENCY_MISSING"

        monkeypatch.setattr(
            plugins.plugin_loader, "plan_install", lambda *args, **kwargs: make_plan()
        )
        monkeypatch.setattr(plugins, "_require_backend_compatibility", lambda plan: None)
        monkeypatch.setattr(
            plugins.plugin_loader,
            "execute_install_plan",
            lambda *args, **kwargs: [("target", "3")],
        )
        result = await plugins.update_plugin(
            {"source": "source", "plugin": "target"}, db
        )
        assert result["status"] == "updated"
        assert result["version"] == "3"

        monkeypatch.setattr(
            plugins.plugin_loader,
            "execute_install_plan",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("failed")),
        )
        with pytest.raises(HTTPException) as failed:
            await plugins.update_plugin(
                {"source": "source", "plugin": "target"}, db
            )
        assert failed.value.status_code == 500


@pytest.mark.asyncio
async def test_remove_plan_reload_and_remove_endpoints(plugin_api_db, monkeypatch):
    with plugin_api_db() as db:
        with pytest.raises(HTTPException) as required:
            await plugins.remove_plan({}, db)
        assert required.value.detail == "PLUGIN_REQUIRED"

        monkeypatch.setattr(
            plugins.plugin_loader,
            "plan_remove",
            lambda db, name: plugins.plugin_loader.RemovePlanResult(plugin=name),
        )
        with pytest.raises(HTTPException) as missing:
            await plugins.remove_plan({"plugin": "target"}, db)
        assert missing.value.status_code == 404

        plan = plugins.plugin_loader.RemovePlanResult(
            plugin="target",
            order=["dependent", "target"],
            dependents=["dependent"],
            human_names={"dependent": "Dependent"},
        )
        monkeypatch.setattr(plugins.plugin_loader, "plan_remove", lambda db, name: plan)
        response = await plugins.remove_plan({"plugin": "target"}, db)
        assert response.remove_order == ["dependent", "target"]

        with pytest.raises(HTTPException) as dependents:
            await plugins.remove_plugin_api({"plugin": "target"}, db)
        assert dependents.value.detail["code"] == "DEPENDENT_PLUGINS"

        removed = []
        monkeypatch.setattr(
            plugins.plugin_loader,
            "remove_plugin",
            lambda name, db: removed.append(name),
        )
        result = await plugins.remove_plugin_api(
            {"plugin": "target", "cascade": True}, db
        )
        assert result["removed"] == ["dependent", "target"]
        assert removed == ["dependent", "target"]

        monkeypatch.setattr(
            plugins.plugin_loader,
            "remove_plugin",
            lambda name, db: (_ for _ in ()).throw(RuntimeError("failed")),
        )
        with pytest.raises(HTTPException) as removal_failed:
            await plugins.remove_plugin_api(
                {"plugin": "target", "cascade": True}, db
            )
        assert removal_failed.value.status_code == 500

        with pytest.raises(HTTPException) as reload_required:
            await plugins.reload_plugin_endpoint(plugins.ReloadRequest(plugin=" "), db)
        assert reload_required.value.detail == "PLUGIN_REQUIRED"

        meta = add_meta(db, "reloaded")
        monkeypatch.setattr(
            plugins.plugin_loader, "reload_plugin", lambda db, name: meta
        )
        assert (
            await plugins.reload_plugin_endpoint(
                plugins.ReloadRequest(plugin=" reloaded "), db
            )
        ) is meta

        for error, status in (
            (FileNotFoundError(), 404),
            (RuntimeError("incompatible"), 409),
            (ValueError("unexpected"), 500),
        ):
            monkeypatch.setattr(
                plugins.plugin_loader,
                "reload_plugin",
                lambda *args, error=error: (_ for _ in ()).throw(error),
            )
            with pytest.raises(HTTPException) as raised:
                await plugins.reload_plugin_endpoint(
                    plugins.ReloadRequest(plugin="target"), db
                )
            assert raised.value.status_code == status
