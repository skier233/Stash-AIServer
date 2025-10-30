from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel
from typing import List, Optional, Any, Dict
import datetime
from sqlalchemy.orm import Session
from sqlalchemy import select, delete
import httpx, traceback  # json unused
from stash_ai_server.utils.string_utils import normalize_null_strings
from stash_ai_server.db.session import SessionLocal
from stash_ai_server.models.plugin import PluginMeta, PluginSource, PluginCatalog, PluginSetting
from stash_ai_server.plugin_runtime import loader as plugin_loader
from stash_ai_server.core.system_settings import SYSTEM_PLUGIN_NAME, get_value as sys_get_value, invalidate_cache as sys_invalidate_cache

router = APIRouter(prefix='/plugins', tags=['plugins'])

# Dependency

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class PluginMetaModel(BaseModel):
    name: str
    version: str
    status: str
    required_backend: str
    migration_head: Optional[str]
    last_error: Optional[str]
    human_name: Optional[str] = None
    server_link: Optional[str] = None
    class Config:
        orm_mode = True

class PluginSourceCreate(BaseModel):
    name: str
    url: str
    enabled: bool = True

class PluginSourceModel(PluginSourceCreate):
    id: int
    last_refreshed_at: Optional[datetime.datetime] = None
    last_error: Optional[str] = None
    class Config:
        orm_mode = True



class PluginSettingModel(BaseModel):
    key: str
    label: Optional[str] = None
    type: str = 'string'
    default: Optional[Any] = None
    options: Optional[Any] = None
    description: Optional[str] = None
    value: Optional[Any] = None

class RefreshResult(BaseModel):
    source: str
    fetched: int
    errors: List[str] = []


class InstallPlanResponse(BaseModel):
    plugin: str
    install_order: List[str]
    dependencies: List[str]
    already_installed: List[str]
    missing: List[str]
    human_names: Dict[str, Optional[str]] = {}


class RemovePlanResponse(BaseModel):
    plugin: str
    remove_order: List[str]
    dependents: List[str]
    human_names: Dict[str, Optional[str]] = {}

@router.get('/installed', response_model=List[PluginMetaModel])
async def list_installed(active_only: bool = False, include_removed: bool = False, db: Session = Depends(get_db)):
    """List plugin metadata rows.

    Query params:
      active_only: if true, return only rows whose status == 'active'
      include_removed: if false, filter out rows with status == 'removed'
    By default we return everything except removed to avoid confusing UI listing.
    """
    q = select(PluginMeta)
    rows = db.execute(q).scalars().all()
    out = []
    for r in rows:
        if active_only and r.status != 'active':
            continue
        if not include_removed and r.status == 'removed':
            continue
        out.append(r)
    return out

@router.get('/sources', response_model=List[PluginSourceModel])
async def list_sources(db: Session = Depends(get_db)):
    rows = db.execute(select(PluginSource)).scalars().all()
    return rows

@router.post('/sources', response_model=PluginSourceModel)
async def create_source(payload: PluginSourceCreate, db: Session = Depends(get_db)):
    existing = db.execute(select(PluginSource).where(PluginSource.name == payload.name)).scalar_one_or_none()
    if existing:
        # Idempotent create: return existing source instead of failing. Tests and
        # callers expect creating the builtin source to be safe repeatedly.
        return existing
    src = PluginSource(name=payload.name, url=payload.url, enabled=payload.enabled)
    db.add(src)
    db.commit()
    db.refresh(src)
    return src

@router.delete('/sources/{source_name}')
async def delete_source(source_name: str, db: Session = Depends(get_db)):
    row = db.execute(select(PluginSource).where(PluginSource.name == source_name)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail='NOT_FOUND')
    db.delete(row)
    db.commit()
    return {'status': 'deleted'}



@router.get('/settings/{plugin_name}', response_model=List[PluginSettingModel])
async def list_plugin_settings(plugin_name: str, db: Session = Depends(get_db)):
    """List stored plugin settings (definitions + current values)."""
    rows = db.execute(select(PluginSetting).where(PluginSetting.plugin_name == plugin_name)).scalars().all()
    return [PluginSettingModel(
        key=r.key,
        label=r.label or r.key,
        type=r.type or 'string',
        default=r.default_value,
        options=r.options,
        description=r.description,
        value=(r.value if r.value is not None else r.default_value)
    ) for r in rows]

class SettingUpsert(BaseModel):
    value: Any | None = None

@router.put('/settings/{plugin_name}/{key}')
async def upsert_setting(plugin_name: str, key: str, payload: SettingUpsert, db: Session = Depends(get_db)):
    row = db.execute(select(PluginSetting).where(PluginSetting.plugin_name == plugin_name, PluginSetting.key == key)).scalar_one_or_none()
    if not row:
        # Create with minimal metadata; caller can later register richer definition
        row = PluginSetting(plugin_name=plugin_name, key=key, type='string', value=None, label=key)
        db.add(row)
        db.commit()
        db.refresh(row)
    # Basic validation by type
    v = payload.value
    if v is not None:
        if row.type == 'number':
            try:
                v = float(v)
            except Exception:
                raise HTTPException(status_code=400, detail='INVALID_NUMBER')
        elif row.type == 'boolean':
            if not isinstance(v, bool):
                # Accept 0/1/'true'/'false'
                if isinstance(v, (int, float)):
                    v = bool(v)
                elif isinstance(v, str) and v.lower() in ('true','false'):
                    v = v.lower() == 'true'
                else:
                    raise HTTPException(status_code=400, detail='INVALID_BOOLEAN')
        elif row.type == 'select' and row.options:
            opts = row.options if isinstance(row.options, list) else []
            if v not in opts:
                raise HTTPException(status_code=400, detail='INVALID_OPTION')
    # Normalize any 'null' string values
    v = normalize_null_strings(v)
    # Null means reset to default
    row.value = v
    db.commit()
    return {'status': 'ok'}

# ---------------- System (global) settings endpoints -----------------

@router.get('/system/settings', response_model=List[PluginSettingModel])
async def list_system_settings(db: Session = Depends(get_db)):
    rows = db.execute(select(PluginSetting).where(PluginSetting.plugin_name == SYSTEM_PLUGIN_NAME)).scalars().all()
    return [PluginSettingModel(
        key=r.key,
        label=r.label or r.key,
        type=r.type or 'string',
        default=r.default_value,
        options=r.options,
        description=r.description,
        value=(r.value if r.value is not None else r.default_value)
    ) for r in rows]

@router.put('/system/settings/{key}')
async def upsert_system_setting(key: str, payload: SettingUpsert, db: Session = Depends(get_db)):
    row = db.execute(select(PluginSetting).where(PluginSetting.plugin_name == SYSTEM_PLUGIN_NAME, PluginSetting.key == key)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail='NOT_FOUND')
    v = payload.value
    if v is not None:
        if row.type == 'number':
            try: v = float(v)
            except Exception: raise HTTPException(status_code=400, detail='INVALID_NUMBER')
        elif row.type == 'boolean':
            if not isinstance(v, bool):
                if isinstance(v, (int,float)): v = bool(v)
                elif isinstance(v, str) and v.lower() in ('true','false'): v = v.lower() == 'true'
                else: raise HTTPException(status_code=400, detail='INVALID_BOOLEAN')
        elif row.type == 'select' and row.options:
            opts = row.options if isinstance(row.options, list) else []
            if v not in opts:
                raise HTTPException(status_code=400, detail='INVALID_OPTION')
    row.value = v
    db.commit()
    sys_invalidate_cache()
    return {'status': 'ok'}



INDEX_EXPECTED_SCHEMA = 1

@router.post('/sources/{source_name}/refresh', response_model=RefreshResult)
async def refresh_source(source_name: str, db: Session = Depends(get_db)):
    src = db.execute(select(PluginSource).where(PluginSource.name == source_name)).scalar_one_or_none()
    if not src:
        raise HTTPException(status_code=404, detail='NOT_FOUND')
    if not src.enabled:
        raise HTTPException(status_code=400, detail='SOURCE_DISABLED')

    errors: List[str] = []
    fetched = 0
    try:
        # Expect root index file at <url>/plugins_index.json (configurable later)
        index_url = src.url.rstrip('/') + '/plugins_index.json'
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(index_url)
        if resp.status_code != 200:
            errors.append(f'HTTP {resp.status_code}')
        else:
            try:
                data = resp.json()
            except Exception as e:  # noqa: BLE001
                errors.append(f'json_parse_error: {e}')
                data = None
            if data:
                schema_ver = data.get('schemaVersion')
                if schema_ver != INDEX_EXPECTED_SCHEMA:
                    errors.append(f'schema_version_mismatch expected={INDEX_EXPECTED_SCHEMA} got={schema_ver}')
                plugins_list = data.get('plugins', []) or []
                # Clear existing catalog entries for source (simpler than diff)
                db.execute(delete(PluginCatalog).where(PluginCatalog.source_id == src.id))
                for entry in plugins_list:
                    try:
                        plugin_name = entry.get('name') or entry.get('plugin_name')
                        if not plugin_name:
                            continue
                        catalog = PluginCatalog(
                            source_id=src.id,
                            plugin_name=plugin_name,
                            version=str(entry.get('version', '0.0.0')),
                            description=entry.get('description'),
                            human_name=entry.get('humanName') or entry.get('human_name'),
                            server_link=entry.get('serverLink') or entry.get('server_link'),
                            dependencies_json={'plugins': entry.get('dependsOn') or entry.get('depends_on') or []},
                            manifest_json=entry,
                        )
                        db.add(catalog)
                        fetched += 1
                    except Exception as e:  # noqa: BLE001
                        errors.append(f'entry_error:{e}')
            src.last_refreshed_at = __import__('datetime').datetime.utcnow()
            if errors:
                src.last_error = ';'.join(errors)[:500]
            else:
                src.last_error = None
            db.commit()
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc(limit=1)
        errors.append(f'exception:{e}')
        src.last_error = str(e)[:500]
        db.commit()
    return RefreshResult(source=source_name, fetched=fetched, errors=errors)


@router.get('/catalog/{source_name}')
async def list_catalog(source_name: str, db: Session = Depends(get_db)):
    # Ensure source exists
    src = db.execute(select(PluginSource).where(PluginSource.name == source_name)).scalar_one_or_none()
    if not src:
        raise HTTPException(status_code=404, detail='NOT_FOUND')
    rows = db.execute(select(PluginCatalog).where(PluginCatalog.source_id == src.id)).scalars().all()
    return [dict(plugin_name=r.plugin_name, version=r.version, description=r.description, manifest=r.manifest_json) for r in rows]


@router.post('/install/plan', response_model=InstallPlanResponse)
async def install_plan(body: dict = Body(...), db: Session = Depends(get_db)):
    plugin_name = body.get('plugin')
    source_name = body.get('source')
    if not plugin_name:
        raise HTTPException(status_code=400, detail='PLUGIN_REQUIRED')
    preferred_source_id = None
    if source_name:
        src = db.execute(select(PluginSource).where(PluginSource.name == source_name)).scalar_one_or_none()
        if not src:
            raise HTTPException(status_code=404, detail='SOURCE_NOT_FOUND')
        preferred_source_id = src.id
    plan = plugin_loader.plan_install(db, plugin_name, preferred_source_id=preferred_source_id)
    if plugin_name not in plan.catalog_rows:
        raise HTTPException(status_code=404, detail='PLUGIN_NOT_FOUND')
    return InstallPlanResponse(
        plugin=plugin_name,
        install_order=plan.order,
        dependencies=plan.dependencies,
        already_installed=plan.already_active,
        missing=plan.missing,
        human_names=plan.human_names,
    )


@router.post('/install')
async def install_plugin(body: dict = Body(...), db: Session = Depends(get_db)):
    # payload: { source: <name>, plugin: <plugin_name>, overwrite: bool }
    source_name = body.get('source')
    plugin_name = body.get('plugin')
    overwrite = bool(body.get('overwrite'))
    install_dependencies = bool(body.get('install_dependencies'))
    src = db.execute(select(PluginSource).where(PluginSource.name == source_name)).scalar_one_or_none()
    if not src:
        raise HTTPException(status_code=404, detail='SOURCE_NOT_FOUND')
    plan = plugin_loader.plan_install(db, plugin_name, preferred_source_id=src.id)
    if plugin_name not in plan.catalog_rows:
        raise HTTPException(status_code=404, detail='PLUGIN_NOT_FOUND')
    if plan.missing:
        raise HTTPException(status_code=400, detail={'code': 'DEPENDENCY_MISSING', 'missing': plan.missing})
    deps_to_install = [name for name in plan.dependencies if name not in plan.already_active]
    if deps_to_install and not install_dependencies:
        raise HTTPException(status_code=409, detail={'code': 'DEPENDENCIES_REQUIRED', 'dependencies': deps_to_install, 'human_names': {n: plan.human_names.get(n) for n in deps_to_install}})
    try:
        installed = plugin_loader.execute_install_plan(db, plan, overwrite_target=overwrite, install_dependencies=install_dependencies or bool(deps_to_install))
        primary_version = next((ver for name, ver in installed if name == plugin_name), None)
        return {'status': 'installed', 'plugin': plugin_name, 'version': primary_version, 'installed': installed}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/update')
async def update_plugin(body: dict = Body(...), db: Session = Depends(get_db)):
    source_name = body.get('source')
    plugin_name = body.get('plugin')
    src = db.execute(select(PluginSource).where(PluginSource.name == source_name)).scalar_one_or_none()
    if not src:
        raise HTTPException(status_code=404, detail='SOURCE_NOT_FOUND')
    plan = plugin_loader.plan_install(db, plugin_name, preferred_source_id=src.id)
    if plugin_name not in plan.catalog_rows:
        raise HTTPException(status_code=404, detail='PLUGIN_NOT_FOUND')
    if plan.missing:
        raise HTTPException(status_code=400, detail={'code': 'DEPENDENCY_MISSING', 'missing': plan.missing})
    try:
        installed = plugin_loader.execute_install_plan(db, plan, overwrite_target=True, install_dependencies=True)
        primary_version = next((ver for name, ver in installed if name == plugin_name), None)
        return {'status': 'updated', 'plugin': plugin_name, 'version': primary_version, 'installed': installed}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/remove/plan', response_model=RemovePlanResponse)
async def remove_plan(body: dict = Body(...), db: Session = Depends(get_db)):
    plugin_name = body.get('plugin')
    if not plugin_name:
        raise HTTPException(status_code=400, detail='PLUGIN_REQUIRED')
    plan = plugin_loader.plan_remove(db, plugin_name)
    if not plan.order:
        raise HTTPException(status_code=404, detail='PLUGIN_NOT_FOUND')
    return RemovePlanResponse(
        plugin=plugin_name,
        remove_order=plan.order,
        dependents=plan.dependents,
        human_names=plan.human_names,
    )


@router.post('/remove')
async def remove_plugin_api(body: dict = Body(...), db: Session = Depends(get_db)):
    plugin_name = body.get('plugin')
    cascade = bool(body.get('cascade'))
    plan = plugin_loader.plan_remove(db, plugin_name)
    if not plan.order:
        raise HTTPException(status_code=404, detail='PLUGIN_NOT_FOUND')
    dependents = [name for name in plan.dependents if name != plugin_name]
    if dependents and not cascade:
        raise HTTPException(status_code=409, detail={'code': 'DEPENDENT_PLUGINS', 'dependents': dependents, 'human_names': {n: plan.human_names.get(n) for n in dependents}})
    removed: List[str] = []
    names_to_remove = plan.order if cascade else [plugin_name]
    try:
        for name in names_to_remove:
            plugin_loader.remove_plugin(name, db)
            removed.append(name)
        return {'status': 'removed', 'plugin': plugin_name, 'removed': removed}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

## Remote catalog 'available' endpoint removed for now; manifest fields supply human_name/server_link.
