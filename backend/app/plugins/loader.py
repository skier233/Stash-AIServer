from __future__ import annotations
import pathlib, yaml, importlib, sys, traceback
from dataclasses import dataclass
from typing import List
from packaging import version as _v  # if packaging not present this will fail; add to requirements if needed
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.db.session import SessionLocal, engine
from app.core.config import settings
from app.models.plugin import PluginMeta

PLUGIN_DIR = pathlib.Path(__file__).parent  # app/plugins

@dataclass
class PluginManifest:
    name: str
    version: str
    required_backend: str
    files: List[str]

def _parse_manifest(path: pathlib.Path) -> PluginManifest | None:
    try:
        data = yaml.safe_load(path.read_text()) or {}
        name = data.get('name')
        ver = data.get('version')
        req = data.get('required_backend')
        files = data.get('files', [])
        if not isinstance(files, list):
            files = []
        if not (name and ver and req):
            print(f"[plugin] invalid manifest missing fields: {path}", flush=True)
            return None
        # Validate folder name matches
        if path.parent.name != name:
            print(f"[plugin] manifest name mismatch dir={path.parent.name} name={name}", flush=True)
            return None
        return PluginManifest(name=name, version=str(ver), required_backend=str(req), files=[str(f) for f in files])
    except Exception as e:
        print(f"[plugin] failed to parse {path}: {e}", flush=True)
        return None

def _backend_version_ok(required: str) -> bool:
    # Very naive: support '>=X.Y.Z' and '<X.Y.Z' tokens separated by commas or spaces
    try:
        cur = _v.parse(settings.version if hasattr(settings, 'version') else '0.0.0')
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
                # treat as exact
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
    db.commit()
    db.refresh(row)
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
            ns = {}
            code = f.read_text()
            exec(compile(code, str(f), 'exec'), ns, ns)
            if 'upgrade' not in ns:
                print(f"[plugin] migration missing upgrade(): {f}", flush=True)
                continue
            with engine.begin() as conn:
                ns['upgrade'](conn)
            meta.migration_head = f.stem
            print(f"[plugin] name={manifest.name} applied_migration={f.name}", flush=True)
        except Exception as e:
            meta.status = 'error'
            meta.last_error = f"migration {f.name} failed: {e}"
            print(f"[plugin] ERROR migration {manifest.name} {f.name}: {e}", flush=True)
            raise

def _import_files(manifest: PluginManifest):
    base_pkg = f"app.plugins.{manifest.name}"
    pkg_root = PLUGIN_DIR / manifest.name
    if str(pkg_root.parent) not in sys.path:
        sys.path.append(str(pkg_root.parent))
    # Ensure package init exists (optional for namespace, but create runtime if needed)
    # Import each listed file (module) relative to plugin root
    for rel in manifest.files:
        mod_path = rel.replace('/', '.').replace('\\', '.')
        full = f"{base_pkg}.{mod_path}"
        try:
            mod = importlib.import_module(full)
            print(f"[plugin] name={manifest.name} imported={full}", flush=True)
            # If module exposes a register() function (pattern used by services), invoke it.
            reg_fn = getattr(mod, 'register', None)
            if callable(reg_fn):
                try:
                    reg_fn()
                    print(f"[plugin] name={manifest.name} invoked register() in {full}", flush=True)
                except Exception as e:
                    print(f"[plugin] name={manifest.name} register() failed in {full}: {e}", flush=True)
        except Exception as e:
            print(f"[plugin] ERROR import {full}: {e}", flush=True)
            raise

def initialize_plugins():
    if not PLUGIN_DIR.exists():
        return
    db = SessionLocal()
    try:
        for manifest_path in PLUGIN_DIR.glob('*/plugin.yml'):
            manifest = _parse_manifest(manifest_path)
            if not manifest:
                continue
            meta = _load_or_create_meta(db, manifest)
            # Check compatibility
            if not _backend_version_ok(manifest.required_backend):
                meta.status = 'incompatible'
                db.commit()
                print(f"[plugin] name={manifest.name} incompatible required_backend={manifest.required_backend}", flush=True)
                continue
            try:
                # Migrations first
                _apply_migrations(manifest, meta)
                # Import declared files (decorators will self-register)
                _import_files(manifest)
                # Update meta
                meta.version = manifest.version
                if meta.status != 'error':
                    meta.status = 'active'
                db.commit()
                print(f"[plugin] name={manifest.name} status={meta.status}", flush=True)
            except Exception:
                meta.status = 'error'
                meta.last_error = traceback.format_exc()[-4000:]
                db.commit()
    finally:
        try:
            db.close()
        except Exception:
            pass
