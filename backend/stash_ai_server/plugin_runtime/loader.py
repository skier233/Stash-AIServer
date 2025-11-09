from __future__ import annotations
"""Plugin loader (migrated from stash_ai_server.plugins.loader).

This file was moved out of the `stash_ai_server.plugins` package so that the `plugins/`
directory can be treated as a pure data/extensions volume at runtime (e.g.
mounted in Docker) while core loader logic ships with the backend image.

Only path constant PLUGIN_DIR still points to the on-disk extensions folder
(`stash_ai_server/plugins`). All previous functionality preserved.
"""
import os, pathlib, yaml, importlib, sys, traceback, tempfile, zipfile, shutil, types, logging
from dataclasses import dataclass, field
from typing import List, Dict, Set, Optional, Tuple, Iterable, Any
from packaging import version as _v
from sqlalchemy.orm import Session
from sqlalchemy import select
from stash_ai_server.db.session import SessionLocal, engine
from stash_ai_server.core.config import settings
from stash_ai_server.models.plugin import PluginMeta, PluginSetting, PluginSource, PluginCatalog
from stash_ai_server.utils.string_utils import normalize_null_strings
from stash_ai_server.plugin_runtime.settings_registry import register_settings
from stash_ai_server.services.registry import services
from stash_ai_server.recommendations.registry import recommender_registry
from stash_ai_server.core.runtime import register_backend_refresh_handler
import httpx
from io import BytesIO

_log = logging.getLogger("stash_ai_server.plugins.loader")


def _sanitize_dependency_list(raw: Any) -> List[str]:
    """Normalize dependency declarations by removing placeholder null strings."""
    normalized = normalize_null_strings(raw)
    if isinstance(normalized, (list, tuple, set)):
        items: Iterable[Any] = normalized
    elif normalized is None:
        return []
    else:
        items = (normalized,)
    cleaned: List[str] = []
    for item in items:
        if item is None:
            continue
        text = str(item).strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in {"null", "none"}:
            continue
        cleaned.append(text)
    return cleaned

# NOTE: We still load plugin python packages from stash_ai_server.plugins.<plugin_name> so
# that individual plugin code remains in that namespace. The loader itself is
# now in stash_ai_server.plugin_runtime. Allow override via AI_SERVER_PLUGINS_DIR so prod
# images can mount plugins externally.

## TODO: move to real config
env_plugins = os.getenv('AI_SERVER_PLUGINS_DIR')
if env_plugins:
    PLUGIN_DIR = pathlib.Path(env_plugins)
else:
    PLUGIN_DIR = pathlib.Path(__file__).resolve().parent.parent / 'plugins'

# Ensure plugin dir exists to avoid repeated existence checks
try:
    PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

# Provide a synthetic namespace package 'stash_ai_server.plugins' pointed at PLUGIN_DIR
# so that import paths like stash_ai_server.plugins.<plugin_name> work even when the
# installed wheel does not ship an 'stash_ai_server.plugins' package containing those
# plugin subpackages (they are runtime additions / mounted volume).
if 'stash_ai_server.plugins' not in sys.modules:
    try:
        parent_pkg = importlib.import_module('stash_ai_server')
        mod = types.ModuleType('stash_ai_server.plugins')
        mod.__path__ = [str(PLUGIN_DIR)]  # namespace path
        setattr(parent_pkg, 'plugins', mod)
        sys.modules['stash_ai_server.plugins'] = mod
    except Exception as e:  # pragma: no cover
        print(f"[plugin] failed to create synthetic namespace: {e}", flush=True)

@dataclass
class PluginManifest:
    name: str
    version: str
    required_backend: str
    files: List[str]
    depends_on: List[str]
    human_name: str | None = None
    server_link: str | None = None
    pip_dependencies: List[str] | None = None


@dataclass
class InstallPlanResult:
    plugin: str
    order: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    already_active: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    catalog_rows: Dict[str, PluginCatalog] = field(default_factory=dict)
    human_names: Dict[str, Optional[str]] = field(default_factory=dict)


@dataclass
class RemovePlanResult:
    plugin: str
    order: List[str] = field(default_factory=list)
    dependents: List[str] = field(default_factory=list)
    human_names: Dict[str, Optional[str]] = field(default_factory=dict)

def _parse_manifest(path: pathlib.Path) -> PluginManifest | None:
    try:
        data = yaml.safe_load(path.read_text()) or {}
        name = data.get('name')
        ver = data.get('version')
        req = data.get('required_backend')
        files = data.get('files', [])
        if not isinstance(files, list):
            files = []
        depends_on = _sanitize_dependency_list(data.get('depends_on'))
        human_name = data.get('human_name') or data.get('title') or data.get('label')
        server_link = data.get('server_link') or data.get('serverLink')
        pip_deps = data.get('pip_dependencies') or data.get('pip-dependencies') or data.get('python_dependencies') or []
        if not isinstance(pip_deps, list):
            pip_deps = []
        if not (name and ver and req):
            print(f"[plugin] invalid manifest missing fields: {path}", flush=True)
            return None
        if path.parent.name != name:
            print(f"[plugin] manifest name mismatch dir={path.parent.name} name={name}", flush=True)
            return None
        return PluginManifest(
            name=name,
            version=str(ver),
            required_backend=str(req),
            files=[str(f) for f in files],
            depends_on=_sanitize_dependency_list(depends_on),
            human_name=human_name,
            server_link=server_link,
            pip_dependencies=[str(p) for p in pip_deps],
        )
    except Exception as e:  # noqa: BLE001
        print(f"[plugin] failed to parse {path}: {e}", flush=True)
        return None


def _load_catalog_row_for_plugin(db: Session, plugin_name: str, preferred_source_id: Optional[int] = None) -> Optional[PluginCatalog]:
    if preferred_source_id is not None:
        row = db.execute(
            select(PluginCatalog).where(PluginCatalog.plugin_name == plugin_name, PluginCatalog.source_id == preferred_source_id)
        ).scalar_one_or_none()
        if row:
            return row
    return db.execute(select(PluginCatalog).where(PluginCatalog.plugin_name == plugin_name)).scalar_one_or_none()


def _catalog_dependencies(row: PluginCatalog) -> List[str]:
    try:
        deps_field = (row.dependencies_json or {}).get('plugins') if isinstance(row.dependencies_json, dict) else None
        cleaned = _sanitize_dependency_list(deps_field)
        if cleaned:
            return cleaned
    except Exception:
        pass
    manifest = row.manifest_json or {}
    raw = manifest.get('dependsOn') or manifest.get('depends_on') or []
    cleaned_manifest = _sanitize_dependency_list(raw)
    return cleaned_manifest


def _catalog_human_name(row: PluginCatalog) -> Optional[str]:
    manifest = row.manifest_json or {}
    return row.human_name or manifest.get('humanName') or manifest.get('human_name') or None

def _settings_definitions_from_raw(raw: dict | None) -> List[dict]:
    if not isinstance(raw, dict):
        return []
    cfg = raw.get('settings') or raw.get('ui_settings') or raw.get('config') or []
    definitions: List[dict] = []
    if isinstance(cfg, dict):
        for key, value in cfg.items():
            if isinstance(value, dict):
                definitions.append({'key': key, **value})
            else:
                definitions.append({'key': key, 'default': value})
    elif isinstance(cfg, list):
        for item in cfg:
            if isinstance(item, dict):
                definitions.append(item)
    return definitions

def _backend_version_ok(required: str) -> bool:
    try:
        cur = _v.parse(getattr(settings, 'version', '0.0.0'))
        parts = [p.strip() for p in required.replace(',', ' ').split() if p.strip()]
        for p in parts:
            if p.startswith('>='):
                if not (cur >= _v.parse(p[2:])): return False
            elif p.startswith('>'):
                if not (cur > _v.parse(p[1:])): return False
            elif p.startswith('<='):
                if not (cur <= _v.parse(p[2:])): return False
            elif p.startswith('<'):
                if not (cur < _v.parse(p[1:])): return False
            elif p.startswith('=='):
                if not (cur == _v.parse(p[2:])): return False
            else:
                if not (cur == _v.parse(p)): return False
        return True
    except Exception:
        return False

def _load_or_create_meta(db: Session, manifest: PluginManifest) -> PluginMeta:
    row = db.execute(select(PluginMeta).where(PluginMeta.name == manifest.name)).scalar_one_or_none()
    if row:
        return row
    row = PluginMeta(name=manifest.name, version=manifest.version, required_backend=manifest.required_backend, status='new')
    db.add(row)
    db.commit(); db.refresh(row)
    return row


def _ensure_local_source(db: Session) -> PluginSource:
    src = db.execute(select(PluginSource).where(PluginSource.name == 'local')).scalar_one_or_none()
    if src:
        return src
    src = PluginSource(name='local', url='local://manual', enabled=True)
    db.add(src)
    db.commit(); db.refresh(src)
    return src


def _ensure_catalog_entry_from_manifest(
    db: Session,
    *,
    manifest: PluginManifest,
    raw_manifest: dict,
) -> None:
    """Ensure a PluginCatalog row exists for on-disk manifests.

    Plugins fetched via remote sources already create catalog rows. For
    drag-and-drop plugins we synthesize a local source entry so the UI can
    surface metadata and dependency details.
    """

    # If a catalog row already exists (remote or local), leave it intact.
    existing = db.execute(select(PluginCatalog).where(PluginCatalog.plugin_name == manifest.name)).scalar_one_or_none()
    if existing:
        return

    src = _ensure_local_source(db)
    dependencies = _sanitize_dependency_list(manifest.depends_on)
    dependencies_json = {'plugins': dependencies} if dependencies else None
    row = PluginCatalog(
        source_id=src.id,
        plugin_name=manifest.name,
        version=manifest.version,
        description=raw_manifest.get('description') if isinstance(raw_manifest, dict) else None,
        human_name=manifest.human_name,
        server_link=manifest.server_link,
        dependencies_json=dependencies_json,
        manifest_json=normalize_null_strings(raw_manifest) if isinstance(raw_manifest, dict) else None,
    )
    db.add(row)
    db.commit()

def _apply_migrations(manifest: PluginManifest, meta: PluginMeta):
    mig_dir = PLUGIN_DIR / manifest.name / 'migrations'
    if not mig_dir.exists():
        return
    files = sorted([p for p in mig_dir.iterdir() if p.name.endswith('.py') and p.name[0:4].isdigit()])
    if not files:
        return
    head = meta.migration_head
    to_apply = []
    for f in files:
        stem = f.stem
        if head is None or stem > head:
            to_apply.append(f)
    if not to_apply:
        return
    for f in to_apply:
        try:
            ns: dict = {}
            code = f.read_text()
            exec(compile(code, str(f), 'exec'), ns, ns)
            if 'upgrade' not in ns:
                print(f"[plugin] migration missing upgrade(): {f}", flush=True)
                continue
            with engine.begin() as conn:
                ns['upgrade'](conn)
            meta.migration_head = f.stem
            print(f"[plugin] name={manifest.name} applied_migration={f.name}", flush=True)
        except Exception as e:  # noqa: BLE001
            meta.status = 'error'
            meta.last_error = f"migration {f.name} failed: {e}"
            print(f"[plugin] ERROR migration {manifest.name} {f.name}: {e}", flush=True)
            raise

def _import_files(manifest: PluginManifest):
    base_pkg = f"stash_ai_server.plugins.{manifest.name}"
    pkg_root = PLUGIN_DIR / manifest.name
    if str(pkg_root.parent) not in sys.path:
        sys.path.append(str(pkg_root.parent))
    for rel in manifest.files:
        mod_path = rel.replace('/', '.').replace('\\', '.')
        full = f"{base_pkg}.{mod_path}"
        try:
            mod = importlib.import_module(full)
            print(f"[plugin] name={manifest.name} imported={full}", flush=True)
            reg_fn = getattr(mod, 'register', None)
            if callable(reg_fn):
                try:
                    reg_fn(); print(f"[plugin] name={manifest.name} invoked register() in {full}", flush=True)
                except Exception as e:  # noqa: BLE001
                    # Surface to stdout and backend logger with stack trace
                    try:
                        print(f"[plugin] name={manifest.name} register() failed in {full}: {e}", flush=True)
                    except Exception:
                        pass
                    try:
                        _log.error("Plugin register() failed plugin=%s module=%s", manifest.name, full, exc_info=True)
                    except Exception:
                        pass
                    raise
        except Exception as e:  # noqa: BLE001
            try:
                print(f"[plugin] ERROR import {full}: {e}", flush=True)
            except Exception:
                pass
            try:
                _log.error("Plugin import failed plugin=%s module=%s", manifest.name, full, exc_info=True)
            except Exception:
                pass
            raise

def _ensure_pip_dependencies(deps: Optional[List[str]]):
    if not deps:
        return
    # Attempt import first to avoid unnecessary installs; simple heuristic using module==pkg or pkg name before extras/versions
    import importlib
    import subprocess, shutil
    for spec in deps:
        base = spec.split('[')[0].split('==')[0].split('>=')[0].split('<=')[0].strip()
        mod_candidate = base.replace('-', '_')
        try:
            importlib.import_module(mod_candidate)
            continue
        except Exception:
            pass
        pip_exe = shutil.which('pip') or shutil.which('pip3')
        if not pip_exe:
            print(f"[plugin] cannot install dependency (pip missing) spec={spec}", flush=True)
            continue
        try:
            print(f"[plugin] installing dependency spec={spec}", flush=True)
            subprocess.check_call([pip_exe, 'install', spec])
        except Exception as e:
            print(f"[plugin] failed to install dependency spec={spec} err={e}", flush=True)


def plan_install(db: Session, plugin_name: str, preferred_source_id: Optional[int] = None) -> InstallPlanResult:
    metas = {m.name: m for m in db.execute(select(PluginMeta)).scalars().all()}
    active_plugins = {name for name, meta in metas.items() if meta and meta.status == 'active'}

    visited: Set[str] = set()
    order: List[str] = []
    missing: List[str] = []
    catalog_rows: Dict[str, PluginCatalog] = {}
    human_names: Dict[str, Optional[str]] = {}

    def dfs(name: str, source_hint: Optional[int]) -> None:
        if name in visited:
            return
        visited.add(name)
        row = _load_catalog_row_for_plugin(db, name, source_hint)
        if not row:
            missing.append(name)
            return
        catalog_rows[name] = row
        human_names[name] = _catalog_human_name(row)
        deps = _catalog_dependencies(row)
        for dep in deps:
            dfs(dep, row.source_id)
        order.append(name)

    dfs(plugin_name, preferred_source_id)
    order = [name for name in order if name in catalog_rows]
    dependencies = [name for name in order if name != plugin_name]
    already_active = sorted({name for name in order if name in active_plugins})
    missing = sorted(set(missing))
    return InstallPlanResult(
        plugin=plugin_name,
        order=order,
        dependencies=dependencies,
        already_active=already_active,
        missing=missing,
        catalog_rows=catalog_rows,
        human_names=human_names,
    )


def execute_install_plan(
    db: Session,
    plan: InstallPlanResult,
    overwrite_target: bool,
    install_dependencies: bool,
) -> List[Tuple[str, str]]:
    installed: List[Tuple[str, str]] = []
    source_cache: Dict[int, PluginSource] = {}
    metas = {m.name: m for m in db.execute(select(PluginMeta)).scalars().all()}

    for name in plan.order:
        row = plan.catalog_rows.get(name)
        if not row:
            continue
        meta = metas.get(name)
        is_target = name == plan.plugin
        if not is_target and meta and meta.status == 'active':
            # Skip already active dependencies
            continue
        if not is_target and not install_dependencies:
            # Safety guard; should have been prevented by caller
            continue
        src = source_cache.get(row.source_id)
        if src is None:
            src = db.execute(select(PluginSource).where(PluginSource.id == row.source_id)).scalar_one_or_none()
            if not src:
                raise RuntimeError(f'source missing for plugin {name}')
            source_cache[row.source_id] = src
        meta = install_plugin_from_catalog(
            db,
            src,
            row,
            overwrite=(overwrite_target if is_target else True),
        )
        installed.append((meta.name, meta.version))
        metas[meta.name] = meta
    return installed


def plan_remove(db: Session, plugin_name: str) -> RemovePlanResult:
    metas = {m.name: m for m in db.execute(select(PluginMeta)).scalars().all()}
    if plugin_name not in metas:
        return RemovePlanResult(plugin=plugin_name, order=[], dependents=[], human_names={})

    graph: Dict[str, List[str]] = {}
    for manifest_path in PLUGIN_DIR.glob('*/plugin.yml'):
        mf = _parse_manifest(manifest_path)
        if not mf:
            continue
        for dep in mf.depends_on:
            graph.setdefault(dep, []).append(mf.name)

    human_names = {name: (meta.human_name if meta else None) for name, meta in metas.items()}

    dependents = [p for p in graph.get(plugin_name, []) if metas.get(p) and metas[p].status != 'removed']

    visited: Set[str] = set()
    order: List[str] = []

    def dfs(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        for child in graph.get(name, []):
            if metas.get(child) and metas[child].status != 'removed':
                dfs(child)
        order.append(name)

    dfs(plugin_name)
    order = [name for name in order if metas.get(name) and metas[name].status != 'removed']
    return RemovePlanResult(plugin=plugin_name, order=order, dependents=dependents, human_names=human_names)

def initialize_plugins():
    if not PLUGIN_DIR.exists():
        return
    db = SessionLocal()
    try:
        try:
            _ensure_builtin_source(db)
        except Exception:
            pass
        manifests: Dict[str, PluginManifest] = {}
        manifest_data_map: Dict[str, dict] = {}
        for manifest_path in PLUGIN_DIR.glob('*/plugin.yml'):
            try:
                raw = yaml.safe_load(manifest_path.read_text()) or {}
            except Exception:
                raw = {}
            m = _parse_manifest(manifest_path)
            if m:
                manifests[m.name] = m; manifest_data_map[m.name] = raw
                try:
                    _ensure_catalog_entry_from_manifest(db, manifest=m, raw_manifest=raw)
                except Exception as e:
                    print(f"[plugin] unable to synthesize catalog entry name={m.name}: {e}", flush=True)
        metas: Dict[str, PluginMeta] = {n: _load_or_create_meta(db, mf) for n, mf in manifests.items()}
        missing_map: Dict[str, List[str]] = {}
        for name, mf in manifests.items():
            missing = [d for d in mf.depends_on if d not in manifests]
            if missing:
                missing_map[name] = missing
                meta = metas[name]; meta.status = 'dependency_missing'; meta.last_error = f"missing deps: {missing}"; db.commit()
                print(f"[plugin] name={name} dependency_missing deps={missing}", flush=True)
        active: Set[str] = set()
        remaining: Set[str] = {n for n in manifests if n not in missing_map}
        progressed = True
        while progressed and remaining:
            progressed = False
            for name in list(remaining):
                mf = manifests[name]
                if any(d not in active for d in mf.depends_on):
                    continue
                meta = metas[name]
                if not _backend_version_ok(mf.required_backend):
                    meta.status = 'incompatible'; db.commit(); remaining.remove(name); progressed = True
                    print(f"[plugin] name={name} incompatible required_backend={mf.required_backend}", flush=True)
                    continue
                try:
                    _apply_migrations(mf, meta)
                    # Ensure pip dependencies if declared
                    try:
                        _ensure_pip_dependencies(mf.pip_dependencies)
                    except Exception as e:
                        print(f"[plugin] dependency install attempt failed name={name}: {e}", flush=True)
                    raw = manifest_data_map.get(name, {})
                    settings_defs = _settings_definitions_from_raw(raw)
                    if settings_defs:
                        try:
                            register_settings(db, mf.name, settings_defs)
                        except Exception as e:  # noqa: BLE001
                            print(f"[plugin] settings registration failed name={mf.name}: {e}", flush=True)
                    _import_files(mf)
                    meta.version = mf.version; meta.human_name = mf.human_name; meta.server_link = mf.server_link
                    meta.status = 'active'
                    meta.last_error = None
                    db.commit(); active.add(name); remaining.remove(name); progressed = True
                    print(f"[plugin] name={name} status={meta.status}", flush=True)
                except Exception:
                    # Capture traceback into DB and also surface to backend logs/terminal
                    tb = traceback.format_exc()[-4000:]
                    meta.status = 'error'
                    meta.last_error = tb
                    db.commit()
                    # Print to stdout for immediate visibility and log with traceback
                    try:
                        print(f"[plugin] ERROR loading name={name}: {tb}", flush=True)
                    except Exception:
                        pass
                    try:
                        _log.error("Plugin load failed name=%s", name, exc_info=True)
                    except Exception:
                        pass
                    remaining.remove(name)
                    progressed = True
        if remaining:
            for name in remaining:
                mf = manifests[name]; meta = metas[name]
                unmet = [d for d in mf.depends_on if d not in active]
                if all(d in remaining for d in unmet) and unmet:
                    meta.status = 'dependency_cycle'; meta.last_error = f"cycle with deps {unmet}"; print(f"[plugin] name={name} dependency_cycle deps={unmet}", flush=True)
                else:
                    meta.status = 'dependency_inactive'; meta.last_error = f"inactive deps: {unmet}"; print(f"[plugin] name={name} dependency_inactive deps={unmet}", flush=True)
                db.commit()
    finally:
        try: db.close()
        except Exception: pass

def _ensure_builtin_source(db: Session):
    existing = db.execute(select(PluginSource).where(PluginSource.name == 'official')).scalar_one_or_none()
    if existing: return existing
    url = 'https://raw.githubusercontent.com/skier233/AIOverhaul_Plugin_Catalog_Official/main'
    src = PluginSource(name='official', url=url, enabled=True)
    db.add(src); db.commit(); db.refresh(src); return src

def _download_repo_subpath_to_dir(repo_base_url: str, subpath: str, dest: pathlib.Path):
    parts = repo_base_url.rstrip('/').split('/')
    owner = repo = branch = None
    if 'raw.githubusercontent.com' in repo_base_url:
        if len(parts) >= 6:
            owner = parts[3]; repo = parts[4]; branch = parts[5]
    else:
        if len(parts) >= 5 and 'github.com' in parts[2]:
            owner = parts[3]; repo = parts[4]; branch = 'main'
    if not (owner and repo and branch):
        raise ValueError('unsupported repo url for api subpath')
    def _fetch_path(path: str, target_dir: pathlib.Path):
        api_url = f'https://api.github.com/repos/{owner}/{repo}/contents/{path}'
        params = {'ref': branch}
        r = httpx.get(api_url, params=params, timeout=30)
        if r.status_code == 404:
            raise FileNotFoundError(f'subpath {path} not found in repo via API')
        r.raise_for_status(); data = r.json()
        if isinstance(data, dict):
            download_url = data.get('download_url');
            if not download_url: raise RuntimeError(f'no download_url for file {path}')
            target_dir.mkdir(parents=True, exist_ok=True)
            filename = pathlib.Path(data.get('name')).name
            dst = target_dir / filename
            rr = httpx.get(download_url, timeout=30); rr.raise_for_status(); dst.write_bytes(rr.content); return
        if isinstance(data, list):
            for entry in data:
                etype = entry.get('type'); name = entry.get('name');
                if not name: continue
                entry_path = entry.get('path')
                if etype == 'file':
                    download_url = entry.get('download_url');
                    if not download_url: continue
                    target_dir.mkdir(parents=True, exist_ok=True)
                    dst = target_dir / name
                    rr = httpx.get(download_url, timeout=30); rr.raise_for_status(); dst.write_bytes(rr.content)
                elif etype == 'dir':
                    _fetch_path(entry_path, target_dir / name)
    _fetch_path(subpath.strip('/'), dest); return True

def install_plugin_from_catalog(db: Session, source_row, catalog_row, overwrite: bool = False):
    plugin_name = catalog_row.plugin_name
    plugin_path_in_repo = (catalog_row.manifest_json or {}).get('path') or (catalog_row.manifest_json or {}).get('plugin_path') or catalog_row.plugin_name
    if not plugin_path_in_repo: raise ValueError('catalog entry missing path')
    target_dir = PLUGIN_DIR / plugin_name
    if target_dir.exists():
        if not overwrite: raise FileExistsError(f'plugin {plugin_name} already exists')
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        _download_repo_subpath_to_dir(source_row.url, plugin_path_in_repo, target_dir)
    except Exception:
        if target_dir.exists(): shutil.rmtree(target_dir); raise
    manifest_file = target_dir / 'plugin.yml'
    manifest = _parse_manifest(manifest_file)
    if not manifest: raise RuntimeError('invalid manifest after extraction')
    meta = _load_or_create_meta(db, manifest)

    # Validate declared plugin dependencies exist on disk before activating.
    missing = [d for d in manifest.depends_on if not (PLUGIN_DIR / d / 'plugin.yml').exists()]
    if missing:
        meta.status = 'dependency_missing'
        meta.last_error = f"missing deps: {missing}"
        db.commit()
        print(f"[plugin] install deferred name={manifest.name} missing_deps={missing}", flush=True)
        return meta

    # Apply migrations and ensure pip deps, then import files to activate plugin.
    try:
        _apply_migrations(manifest, meta)
        try:
            _ensure_pip_dependencies(manifest.pip_dependencies)
        except Exception as e:
            print(f"[plugin] dependency install attempt failed name={manifest.name}: {e}", flush=True)
        try:
            raw_manifest = yaml.safe_load(manifest_file.read_text()) or {}
        except Exception:
            raw_manifest = {}
        settings_defs = _settings_definitions_from_raw(raw_manifest if isinstance(raw_manifest, dict) else {})
        if settings_defs:
            try:
                register_settings(db, manifest.name, settings_defs)
            except Exception as e:  # noqa: BLE001
                print(f"[plugin] settings registration failed name={manifest.name}: {e}", flush=True)
        _import_files(manifest)
        meta.version = manifest.version; meta.human_name = manifest.human_name; meta.server_link = manifest.server_link
        meta.status = 'active'
        meta.last_error = None
        db.commit(); return meta
    except Exception as e:
        meta.status = 'error'; meta.last_error = str(e)
        db.commit()
        raise

def remove_plugin(plugin_name: str, db: Session):
    # Attempt to gracefully unload plugin from the running process first
    try:
        _unload_plugin(plugin_name)
    except Exception as e:
        print(f"[plugin] unload failed name={plugin_name} err={e}", flush=True)

    target_dir = PLUGIN_DIR / plugin_name
    if target_dir.exists(): shutil.rmtree(target_dir)

    # Remove plugin settings rows
    try:
        existing_settings = db.execute(select(PluginSetting).where(PluginSetting.plugin_name == plugin_name)).scalars().all()
        for r in existing_settings:
            try: db.delete(r)
            except Exception:
                pass
    except Exception:
        pass

    # Mark meta removed
    row = db.execute(select(PluginMeta).where(PluginMeta.name == plugin_name)).scalar_one_or_none()
    if row:
        row.status = 'removed'; db.commit()

    # Cascade: mark any plugins depending on this plugin as missing and unload them too
    try:
        for manifest_path in PLUGIN_DIR.glob('*/plugin.yml'):
            mf = _parse_manifest(manifest_path)
            if not mf: continue
            if plugin_name in mf.depends_on:
                other_meta = db.execute(select(PluginMeta).where(PluginMeta.name == mf.name)).scalar_one_or_none()
                if other_meta:
                    other_meta.status = 'dependency_missing'
                    other_meta.last_error = f"missing deps: ['{plugin_name}']"
                    db.commit()
                try:
                    _unload_plugin(mf.name)
                except Exception:
                    pass
    except Exception:
        pass

    return True


def _unload_plugin(plugin_name: str):
    """Attempt to call unregister() for loaded plugin modules and remove them from sys.modules.
    This allows removals to take effect immediately without a server restart."""
    prefix = f"stash_ai_server.plugins.{plugin_name}"
    try:
        services.unregister_by_plugin(plugin_name)
    except Exception:
        pass
    try:
        recommender_registry.unregister_by_module_prefix(prefix)
    except Exception:
        pass
    # Collect keys first to avoid mutating while iterating
    keys = [k for k in list(sys.modules.keys()) if k == prefix or k.startswith(prefix + '.')]
    for k in keys:
        mod = sys.modules.get(k)
        if not mod:
            continue
        try:
            un = getattr(mod, 'unregister', None)
            if callable(un):
                try:
                    un()
                    print(f"[plugin] name={plugin_name} called unregister in {k}", flush=True)
                except Exception as e:
                    print(f"[plugin] name={plugin_name} unregister() failed in {k}: {e}", flush=True)
        except Exception:
            pass
        try:
            del sys.modules[k]
        except Exception:
            pass
    try:
        importlib.invalidate_caches()
    except Exception:
        pass


def reload_plugin(db: Session, plugin_name: str) -> PluginMeta:
    manifest_path = PLUGIN_DIR / plugin_name / 'plugin.yml'
    if not manifest_path.exists():
        raise FileNotFoundError(f'plugin manifest missing for {plugin_name}')
    try:
        raw = yaml.safe_load(manifest_path.read_text()) or {}
    except Exception:
        raw = {}
    manifest = _parse_manifest(manifest_path)
    if manifest is None:
        raise RuntimeError(f'invalid manifest for plugin {plugin_name}')
    meta = _load_or_create_meta(db, manifest)

    if not _backend_version_ok(manifest.required_backend):
        meta.status = 'incompatible'
        meta.last_error = f"requires backend {manifest.required_backend}"
        db.commit()
        raise RuntimeError(f'backend version incompatible for {plugin_name}')

    missing = [d for d in manifest.depends_on if not (PLUGIN_DIR / d / 'plugin.yml').exists()]
    if missing:
        meta.status = 'dependency_missing'
        meta.last_error = f"missing deps: {missing}"
        db.commit()
        raise RuntimeError(f'missing dependencies {missing} for {plugin_name}')

    try:
        _ensure_catalog_entry_from_manifest(db, manifest=manifest, raw_manifest=raw if isinstance(raw, dict) else {})
    except Exception:
        pass

    try:
        _unload_plugin(plugin_name)
    except Exception:
        pass

    try:
        _apply_migrations(manifest, meta)
        try:
            _ensure_pip_dependencies(manifest.pip_dependencies)
        except Exception as e:
            print(f"[plugin] dependency install attempt failed name={plugin_name}: {e}", flush=True)
        settings_defs = _settings_definitions_from_raw(raw if isinstance(raw, dict) else {})
        if settings_defs:
            try:
                register_settings(db, manifest.name, settings_defs)
            except Exception as e:  # noqa: BLE001
                print(f"[plugin] settings registration failed name={manifest.name}: {e}", flush=True)
        _import_files(manifest)
        meta.version = manifest.version
        meta.human_name = manifest.human_name
        meta.server_link = manifest.server_link
        meta.status = 'active'
        meta.last_error = None
        db.commit()
        return meta
    except Exception as exc:
        if meta.status != 'error':
            meta.status = 'error'
            meta.last_error = str(exc)
        db.commit()
        raise


def reload_all_plugins() -> None:
    """Best-effort reload for every on-disk plugin manifest."""

    manifests = sorted(PLUGIN_DIR.glob('*/plugin.yml'))
    if not manifests:
        return
    db = SessionLocal()
    try:
        try:
            _ensure_builtin_source(db)
        except Exception:
            pass
        for manifest_path in manifests:
            plugin_name = manifest_path.parent.name
            try:
                _unload_plugin(plugin_name)
            except Exception:
                _log.exception("failed unloading plugin %s before reload", plugin_name)
            try:
                reload_plugin(db, plugin_name)
            except Exception:
                db.rollback()
                print(f"[plugin] reload failed for {plugin_name}", flush=True)
                _log.exception("plugin reload failed for %s", plugin_name)
    finally:
        try:
            db.close()
        except Exception:
            pass


def _refresh_plugins() -> None:
    try:
        reload_all_plugins()
    except Exception:
        _log.exception("plugin refresh handler failed")


register_backend_refresh_handler('all_plugins', _refresh_plugins, priority=100)
