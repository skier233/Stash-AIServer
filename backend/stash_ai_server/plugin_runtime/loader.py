from __future__ import annotations
"""Plugin loader (migrated from stash_ai_server.plugins.loader).

This file was moved out of the `stash_ai_server.plugins` package so that the `plugins/`
directory can be treated as a pure data/extensions volume at runtime (e.g.
mounted in Docker) while core loader logic ships with the backend image.

Only path constant PLUGIN_DIR still points to the on-disk extensions folder
(`stash_ai_server/plugins`). All previous functionality preserved.
"""
import os, pathlib, yaml, importlib, sys, traceback, tempfile, zipfile, shutil, types
from dataclasses import dataclass
from typing import List, Dict, Set, Optional
from packaging import version as _v
from sqlalchemy.orm import Session
from sqlalchemy import select
from stash_ai_server.db.session import SessionLocal, engine
from stash_ai_server.core.config import settings
from stash_ai_server.models.plugin import PluginMeta, PluginSetting, PluginSource
from stash_ai_server.plugin_runtime.settings_registry import register_settings
import httpx
from io import BytesIO

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

def _parse_manifest(path: pathlib.Path) -> PluginManifest | None:
    try:
        data = yaml.safe_load(path.read_text()) or {}
        name = data.get('name')
        ver = data.get('version')
        req = data.get('required_backend')
        files = data.get('files', [])
        if not isinstance(files, list):
            files = []
        depends_on = data.get('depends_on', []) or []
        if not isinstance(depends_on, list):
            depends_on = []
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
        return PluginManifest(name=name, version=str(ver), required_backend=str(req), files=[str(f) for f in files], depends_on=[str(d) for d in depends_on], human_name=human_name, server_link=server_link, pip_dependencies=[str(p) for p in pip_deps])
    except Exception as e:  # noqa: BLE001
        print(f"[plugin] failed to parse {path}: {e}", flush=True)
        return None

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
                    print(f"[plugin] name={manifest.name} register() failed in {full}: {e}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[plugin] ERROR import {full}: {e}", flush=True)
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
                    settings_defs = []
                    cfg = raw.get('settings') or raw.get('ui_settings') or raw.get('config') or []
                    if isinstance(cfg, dict):
                        for k, v in cfg.items():
                            if isinstance(v, dict): settings_defs.append({'key': k, **v})
                            else: settings_defs.append({'key': k, 'default': v})
                    elif isinstance(cfg, list):
                        for item in cfg:
                            if isinstance(item, dict): settings_defs.append(item)
                    if settings_defs:
                        try: register_settings(db, mf.name, settings_defs)
                        except Exception as e:  # noqa: BLE001
                            print(f"[plugin] settings registration failed name={mf.name}: {e}", flush=True)
                    _import_files(mf)
                    meta.version = mf.version; meta.human_name = mf.human_name; meta.server_link = mf.server_link
                    if meta.status != 'error': meta.status = 'active'
                    db.commit(); active.add(name); remaining.remove(name); progressed = True
                    print(f"[plugin] name={name} status={meta.status}", flush=True)
                except Exception:
                    meta.status = 'error'; meta.last_error = traceback.format_exc()[-4000:]; db.commit(); remaining.remove(name); progressed = True
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
    _import_files(manifest)
    meta.version = manifest.version; meta.human_name = manifest.human_name; meta.server_link = manifest.server_link
    if meta.status != 'error': meta.status = 'active'
    db.commit(); return meta

def remove_plugin(plugin_name: str, db: Session):
    target_dir = PLUGIN_DIR / plugin_name
    if target_dir.exists(): shutil.rmtree(target_dir)
    row = db.execute(select(PluginMeta).where(PluginMeta.name == plugin_name)).scalar_one_or_none()
    if row:
        row.status = 'removed'; db.commit()
    return True
