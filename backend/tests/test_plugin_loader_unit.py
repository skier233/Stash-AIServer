from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from fastapi import APIRouter
from sqlalchemy.orm import sessionmaker

from stash_ai_server.models.plugin import PluginCatalog
from stash_ai_server.models.plugin import PluginMeta
from stash_ai_server.models.plugin import PluginSetting
from stash_ai_server.models.plugin import PluginSource
from stash_ai_server.plugin_runtime import loader


@pytest.fixture
def plugin_environment(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'plugins.sqlite'}")
    for table in (
        PluginSource.__table__,
        PluginCatalog.__table__,
        PluginMeta.__table__,
        PluginSetting.__table__,
    ):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(loader, "PLUGIN_DIR", plugin_dir)
    namespace = sys.modules.get("stash_ai_server.plugins")
    old_path = list(namespace.__path__) if namespace is not None else None
    if namespace is not None:
        namespace.__path__ = [str(plugin_dir)]
    try:
        yield plugin_dir, factory
    finally:
        if namespace is not None and old_path is not None:
            namespace.__path__ = old_path
        engine.dispose()


def write_manifest(plugin_dir: Path, name: str, **overrides) -> Path:
    directory = plugin_dir / name
    directory.mkdir(parents=True, exist_ok=True)
    values = {
        "name": name,
        "version": "1.0.0",
        "required_backend": ">=0.9",
        "files": [],
        "depends_on": [],
    }
    values.update(overrides)
    import yaml

    path = directory / "plugin.yml"
    path.write_text(yaml.safe_dump(values))
    return path


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, []),
        ("plugin", ["plugin"]),
        ([" one ", "", None, "null", "none", 2], ["one", "2"]),
        (("a", "b"), ["a", "b"]),
    ],
)
def test_sanitize_dependency_list(raw, expected):
    assert loader._sanitize_dependency_list(raw) == expected


def test_parse_manifest_supports_aliases_coercion_and_rejects_invalid_manifests(
    plugin_environment,
):
    plugin_dir, _ = plugin_environment
    valid = write_manifest(
        plugin_dir,
        "example",
        version=2,
        files=["module", 3],
        depends_on=["dependency", "null"],
        title="Example Plugin",
        serverLink="https://example.test",
        python_dependencies=["package>=1"],
    )
    manifest = loader._parse_manifest(valid)
    assert manifest == loader.PluginManifest(
        name="example",
        version="2",
        required_backend=">=0.9",
        files=["module", "3"],
        depends_on=["dependency"],
        human_name="Example Plugin",
        server_link="https://example.test",
        pip_dependencies=["package>=1"],
    )

    invalid = write_manifest(plugin_dir, "invalid", required_backend=None)
    assert loader._parse_manifest(invalid) is None

    mismatch = write_manifest(plugin_dir, "mismatch")
    mismatch.write_text(mismatch.read_text().replace("name: mismatch", "name: other"))
    assert loader._parse_manifest(mismatch) is None

    non_list_files = write_manifest(plugin_dir, "non_list_files", files="module")
    assert loader._parse_manifest(non_list_files).files == []

    malformed = plugin_dir / "broken" / "plugin.yml"
    malformed.parent.mkdir()
    malformed.write_text("[")
    assert loader._parse_manifest(malformed) is None


def test_catalog_and_settings_helpers_normalize_fallback_fields(monkeypatch):
    row = SimpleNamespace(
        dependencies_json={"plugins": ["direct", "null"]},
        manifest_json={
            "dependsOn": ["fallback"],
            "requiredBackend": " >=1 ",
            "humanName": "Human",
        },
        required_backend=None,
        human_name=None,
    )
    assert loader._catalog_dependencies(row) == ["direct"]
    row.dependencies_json = None
    assert loader._catalog_dependencies(row) == ["fallback"]
    assert loader._catalog_required_backend(row) == ">=1"
    assert loader._catalog_human_name(row) == "Human"

    assert loader._settings_definitions_from_raw(None) == []
    assert loader._settings_definitions_from_raw(
        {"settings": {"enabled": {"type": "boolean"}, "limit": 4}}
    ) == [
        {"key": "enabled", "type": "boolean"},
        {"key": "limit", "default": 4},
    ]
    assert loader._settings_definitions_from_raw(
        {"ui_settings": [{"key": "one"}, "invalid"]}
    ) == [{"key": "one"}]

    captured = []
    monkeypatch.setattr(
        loader,
        "version_satisfies",
        lambda version, required: captured.append((version, required)) or True,
    )
    assert loader._backend_version_ok(">=1") is True
    assert captured[0][1] == ">=1"
    assert "requires backend >=1" in loader._format_backend_incompatibility(">=1")

    meta = SimpleNamespace(status=None, last_error=None)
    loader._mark_incompatible(meta, ">=2")
    assert meta.status == "incompatible"
    assert ">=2" in meta.last_error


def test_meta_source_and_catalog_creation_are_idempotent(plugin_environment):
    plugin_dir, factory = plugin_environment
    manifest = loader.PluginManifest(
        name="example",
        version="1",
        required_backend=">=0",
        files=[],
        depends_on=["dependency"],
        human_name="Example",
        server_link="https://example.test",
    )
    with factory() as db:
        meta = loader._load_or_create_meta(db, manifest)
        assert meta.status == "new"
        assert loader._load_or_create_meta(db, manifest).id == meta.id

        local = loader._ensure_local_source(db)
        assert loader._ensure_local_source(db).id == local.id

        loader._ensure_catalog_entry_from_manifest(
            db,
            manifest=manifest,
            raw_manifest={"description": "Description", "extra": "null"},
        )
        loader._ensure_catalog_entry_from_manifest(
            db, manifest=manifest, raw_manifest={}
        )
        catalog = db.scalar(sa.select(PluginCatalog))
        assert catalog.dependencies_json == {"plugins": ["dependency"]}
        assert catalog.manifest_json["extra"] is None


def test_load_catalog_row_prefers_requested_source(plugin_environment):
    _, factory = plugin_environment
    with factory() as db:
        first = PluginSource(name="first", url="one", enabled=True)
        second = PluginSource(name="second", url="two", enabled=True)
        db.add_all([first, second])
        db.flush()
        db.add_all(
            [
                PluginCatalog(
                    source_id=first.id, plugin_name="plugin", version="1"
                ),
                PluginCatalog(
                    source_id=second.id, plugin_name="plugin", version="2"
                ),
            ]
        )
        db.commit()

        assert (
            loader._load_catalog_row_for_plugin(
                db, "plugin", preferred_source_id=second.id
            ).version
            == "2"
        )
        assert loader._load_catalog_row_for_plugin(db, "missing") is None


def test_apply_migrations_runs_pending_files_and_records_failures(
    plugin_environment, monkeypatch
):
    plugin_dir, factory = plugin_environment
    manifest_path = write_manifest(plugin_dir, "example")
    manifest = loader._parse_manifest(manifest_path)
    migrations = manifest_path.parent / "migrations"
    migrations.mkdir()
    (migrations / "0001_first.py").write_text(
        "def upgrade(connection):\n    connection.exec_driver_sql('CREATE TABLE migrated (id INTEGER)')\n"
    )
    (migrations / "0002_missing.py").write_text("value = 1\n")
    probe_session = factory()
    try:
        engine = probe_session.get_bind()
    finally:
        probe_session.close()
    monkeypatch.setattr(loader, "get_engine", lambda: engine)

    meta = SimpleNamespace(migration_head=None, status="new", last_error=None)
    loader._apply_migrations(manifest, meta)
    assert meta.migration_head == "0001_first"
    loader._apply_migrations(manifest, meta)

    (migrations / "0003_error.py").write_text(
        "def upgrade(connection):\n    raise RuntimeError('migration failed')\n"
    )
    with pytest.raises(RuntimeError):
        loader._apply_migrations(manifest, meta)
    assert meta.status == "error"
    assert "0003_error.py" in meta.last_error


def test_import_files_invokes_register_and_surfaces_failures(monkeypatch, plugin_environment):
    plugin_dir, _ = plugin_environment
    monkeypatch.setattr(sys, "path", list(sys.path))
    manifest = loader.PluginManifest(
        name="example",
        version="1",
        required_backend=">=0",
        files=["one", "nested/two"],
        depends_on=[],
    )
    calls = []
    modules = {
        "stash_ai_server.plugins.example.one": SimpleNamespace(
            register=lambda: calls.append("one")
        ),
        "stash_ai_server.plugins.example.nested.two": SimpleNamespace(
            register=lambda: calls.append("two")
        ),
    }
    monkeypatch.setattr(
        loader,
        "importlib",
        SimpleNamespace(import_module=modules.__getitem__),
    )
    loader._import_files(manifest)
    assert calls == ["one", "two"]

    modules["stash_ai_server.plugins.example.one"] = SimpleNamespace(
        register=lambda: (_ for _ in ()).throw(RuntimeError("register failed"))
    )
    with pytest.raises(RuntimeError):
        loader._import_files(manifest)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="The fake pip executable uses a POSIX shell",
)
def test_ensure_pip_dependencies_skips_importable_and_attempts_missing(
    monkeypatch, tmp_path
):
    present = types.ModuleType("present")
    monkeypatch.setitem(sys.modules, "present", present)
    marker = tmp_path / "pip-arguments.txt"
    pip_executable = tmp_path / "pip"
    pip_executable.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$PLUGIN_TEST_MARKER\"\n"
    )
    pip_executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path), prepend=os.pathsep)
    monkeypatch.setenv("PLUGIN_TEST_MARKER", str(marker))

    loader._ensure_pip_dependencies(
        ["present>=1", "missing-package[extra]==2"]
    )
    assert marker.read_text().splitlines() == [
        "install",
        "missing-package[extra]==2",
    ]
    loader._ensure_pip_dependencies([])


def _seed_install_graph(db):
    source = PluginSource(name="source", url="https://example.test", enabled=True)
    db.add(source)
    db.flush()
    rows = [
        PluginCatalog(
            source_id=source.id,
            plugin_name="dependency",
            version="1",
            human_name="Dependency",
            manifest_json={"required_backend": ">=0"},
        ),
        PluginCatalog(
            source_id=source.id,
            plugin_name="target",
            version="2",
            dependencies_json={"plugins": ["dependency", "missing"]},
            manifest_json={"required_backend": ">=0"},
        ),
    ]
    db.add_all(rows)
    db.add(
        PluginMeta(
            name="dependency",
            version="1",
            required_backend=">=0",
            status="active",
        )
    )
    db.commit()
    return source, rows


def test_plan_install_resolves_dependencies_active_and_missing(plugin_environment):
    _, factory = plugin_environment
    with factory() as db:
        source, _ = _seed_install_graph(db)
        plan = loader.plan_install(db, "target", preferred_source_id=source.id)
        assert plan.order == ["dependency", "target"]
        assert plan.dependencies == ["dependency"]
        assert plan.already_active == ["dependency"]
        assert plan.missing == ["missing"]
        assert plan.human_names["dependency"] == "Dependency"
        assert plan.required_backend["target"] == ">=0"


def test_execute_install_plan_honors_active_dependencies_and_source_errors(
    plugin_environment, monkeypatch
):
    _, factory = plugin_environment
    with factory() as db:
        source, rows = _seed_install_graph(db)
        plan = loader.InstallPlanResult(
            plugin="target",
            order=["dependency", "target"],
            catalog_rows={row.plugin_name: row for row in rows},
        )
        calls = []
        monkeypatch.setattr(
            loader,
            "install_plugin_from_catalog",
            lambda db, src, row, overwrite=False: calls.append(
                (row.plugin_name, overwrite)
            )
            or PluginMeta(
                name=row.plugin_name,
                version=row.version,
                required_backend=">=0",
                status="active",
            ),
        )
        assert loader.execute_install_plan(
            db, plan, overwrite_target=False, install_dependencies=False
        ) == [("target", "2")]
        assert calls == [("target", False)]

        missing_source_row = SimpleNamespace(
            source_id=999,
            plugin_name="target",
        )
        with pytest.raises(RuntimeError):
            loader.execute_install_plan(
                db,
                loader.InstallPlanResult(
                    plugin="target",
                    order=["target"],
                    catalog_rows={"target": missing_source_row},
                ),
                overwrite_target=True,
                install_dependencies=True,
            )


def test_plan_remove_orders_dependents_before_target(plugin_environment):
    plugin_dir, factory = plugin_environment
    write_manifest(plugin_dir, "base")
    write_manifest(plugin_dir, "child", depends_on=["base"])
    with factory() as db:
        db.add_all(
            [
                PluginMeta(
                    name="base",
                    version="1",
                    required_backend=">=0",
                    status="active",
                    human_name="Base",
                ),
                PluginMeta(
                    name="child",
                    version="1",
                    required_backend=">=0",
                    status="active",
                    human_name="Child",
                ),
            ]
        )
        db.commit()
        plan = loader.plan_remove(db, "base")
        assert plan.order == ["child", "base"]
        assert plan.dependents == ["child"]
        assert plan.human_names == {"base": "Base", "child": "Child"}
        assert loader.plan_remove(db, "missing").order == []


def test_initialize_plugins_handles_active_missing_incompatible_cycle_and_error(
    plugin_environment, monkeypatch
):
    plugin_dir, factory = plugin_environment
    write_manifest(plugin_dir, "dependency", files=["module"])
    write_manifest(plugin_dir, "active", depends_on=["dependency"], files=["module"])
    write_manifest(plugin_dir, "missing", depends_on=["absent"])
    write_manifest(plugin_dir, "incompatible", required_backend=">=99")
    write_manifest(plugin_dir, "cycle_a", depends_on=["cycle_b"])
    write_manifest(plugin_dir, "cycle_b", depends_on=["cycle_a"])
    write_manifest(plugin_dir, "error", files=["module"])

    db = factory()
    monkeypatch.setattr(loader, "get_session", lambda: db)
    monkeypatch.setattr(
        loader,
        "_backend_version_ok",
        lambda required: required != ">=99",
    )
    monkeypatch.setattr(loader, "_apply_migrations", lambda *args: None)
    monkeypatch.setattr(loader, "_ensure_pip_dependencies", lambda deps: None)
    monkeypatch.setattr(loader, "register_settings", lambda *args: None)

    def import_files(manifest):
        if manifest.name == "error":
            raise RuntimeError("load failed")

    monkeypatch.setattr(loader, "_import_files", import_files)
    loader.initialize_plugins()

    with factory() as verify:
        statuses = {
            row.name: row.status for row in verify.scalars(sa.select(PluginMeta)).all()
        }
        assert statuses["dependency"] == "active"
        assert statuses["active"] == "active"
        assert statuses["missing"] == "dependency_missing"
        assert statuses["incompatible"] == "incompatible"
        assert statuses["cycle_a"] == "dependency_cycle"
        assert statuses["cycle_b"] == "dependency_cycle"
        assert statuses["error"] == "error"
        assert verify.scalar(
            sa.select(PluginSource).where(PluginSource.name == "official")
        ) is not None


def test_builtin_source_and_router_registry(plugin_environment):
    _, factory = plugin_environment
    with factory() as db:
        first = loader._ensure_builtin_source(db)
        second = loader._ensure_builtin_source(db)
        assert first.id == second.id

    old = loader._plugin_routers.copy()
    try:
        loader._plugin_routers.clear()
        router = APIRouter()
        loader.register_plugin_router("example", router)
        returned = loader.get_plugin_routers()
        assert returned == {"example": router}
        returned.clear()
        assert loader.get_plugin_routers() == {"example": router}
    finally:
        loader._plugin_routers.clear()
        loader._plugin_routers.update(old)


def test_download_repo_subpath_recurses_and_writes_files(
    plugin_environment, monkeypatch
):
    plugin_dir, _ = plugin_environment
    responses = {
        "https://api.github.com/repos/owner/repo/contents/root": SimpleNamespace(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: [
                {
                    "type": "file",
                    "name": "one.txt",
                    "path": "root/one.txt",
                    "download_url": "https://download/one",
                },
                {
                    "type": "dir",
                    "name": "nested",
                    "path": "root/nested",
                },
                {"type": "file", "name": "", "path": "ignored"},
            ],
        ),
        "https://api.github.com/repos/owner/repo/contents/root/nested": SimpleNamespace(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {
                "type": "file",
                "name": "two.txt",
                "download_url": "https://download/two",
            },
        ),
        "https://download/one": SimpleNamespace(
            content=b"one", raise_for_status=lambda: None
        ),
        "https://download/two": SimpleNamespace(
            content=b"two", raise_for_status=lambda: None
        ),
    }
    monkeypatch.setattr(
        loader,
        "httpx",
        SimpleNamespace(get=lambda url, **kwargs: responses[url]),
    )
    destination = plugin_dir / "download"
    assert loader._download_repo_subpath_to_dir(
        "https://raw.githubusercontent.com/owner/repo/main",
        "root",
        destination,
    )
    assert (destination / "one.txt").read_bytes() == b"one"
    assert (destination / "nested" / "two.txt").read_bytes() == b"two"

    with pytest.raises(ValueError):
        loader._download_repo_subpath_to_dir("https://unsupported.test", "x", destination)


def test_install_remove_reload_and_unload_plugin(
    plugin_environment, monkeypatch
):
    plugin_dir, factory = plugin_environment
    source = SimpleNamespace(url="https://example.test")
    catalog = SimpleNamespace(
        plugin_name="example",
        manifest_json={"path": "example"},
    )

    def download(_url, _path, destination):
        write_manifest(
            destination.parent,
            destination.name,
            files=["module"],
            settings={"enabled": {"type": "boolean"}},
        )

    monkeypatch.setattr(loader, "_download_repo_subpath_to_dir", download)
    monkeypatch.setattr(loader, "_backend_version_ok", lambda required: True)
    monkeypatch.setattr(loader, "_apply_migrations", lambda *args: None)
    monkeypatch.setattr(loader, "_ensure_pip_dependencies", lambda deps: None)
    monkeypatch.setattr(loader, "_import_files", lambda manifest: None)
    settings_calls = []
    monkeypatch.setattr(
        loader,
        "register_settings",
        lambda db, name, definitions: settings_calls.append((name, definitions)),
    )
    monkeypatch.setattr(loader, "_unload_plugin", lambda name: None)

    with factory() as db:
        meta = loader.install_plugin_from_catalog(db, source, catalog)
        assert meta.status == "active"
        assert settings_calls[0][0] == "example"
        with pytest.raises(FileExistsError):
            loader.install_plugin_from_catalog(db, source, catalog)

        reloaded = loader.reload_plugin(db, "example")
        assert reloaded.status == "active"

        db.add(
            PluginSetting(
                plugin_name="example",
                key="enabled",
                type="boolean",
            )
        )
        db.commit()
        assert loader.remove_plugin("example", db) is True
        assert meta.status == "removed"
        assert not (plugin_dir / "example").exists()

        with pytest.raises(FileNotFoundError):
            loader.reload_plugin(db, "missing")


def test_unload_plugin_unregisters_modules_and_invalidates_caches(monkeypatch):
    prefix = "stash_ai_server.plugins.example"
    calls = []
    module = types.ModuleType(f"{prefix}.module")
    module.unregister = lambda: calls.append("unregister")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(
        loader.services,
        "unregister_by_plugin",
        lambda name: calls.append(("service", name)),
    )
    monkeypatch.setattr(
        loader.recommender_registry,
        "unregister_by_module_prefix",
        lambda name: calls.append(("recommender", name)),
    )
    monkeypatch.setattr(
        loader,
        "importlib",
        SimpleNamespace(invalidate_caches=lambda: calls.append("invalidate")),
    )

    loader._unload_plugin("example")

    assert module.__name__ not in sys.modules
    assert "unregister" in calls
    assert ("service", "example") in calls
    assert ("recommender", prefix) in calls
    assert "invalidate" in calls


def test_reload_all_and_refresh_are_best_effort(plugin_environment, monkeypatch):
    plugin_dir, factory = plugin_environment
    write_manifest(plugin_dir, "one")
    write_manifest(plugin_dir, "two")
    db = factory()
    monkeypatch.setattr(loader, "get_session", lambda: db)
    calls = []
    monkeypatch.setattr(loader, "_ensure_builtin_source", lambda db: None)
    monkeypatch.setattr(loader, "_unload_plugin", lambda name: calls.append(("unload", name)))

    def reload_plugin(db, name):
        calls.append(("reload", name))
        if name == "two":
            raise RuntimeError("failed")

    monkeypatch.setattr(loader, "reload_plugin", reload_plugin)
    loader.reload_all_plugins()
    assert ("reload", "one") in calls
    assert ("reload", "two") in calls

    monkeypatch.setattr(loader, "reload_all_plugins", lambda: calls.append("refresh"))
    loader._refresh_plugins()
    assert "refresh" in calls
