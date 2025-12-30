from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel
from typing import List, Optional, Any, Dict, Tuple
import datetime
import json
from sqlalchemy.orm import Session
from sqlalchemy import select, delete
import httpx, traceback
from stash_ai_server.utils.string_utils import normalize_null_strings
from stash_ai_server.db.session import SessionLocal
from stash_ai_server.models.plugin import PluginMeta, PluginSource, PluginCatalog, PluginSetting
from stash_ai_server.plugin_runtime import loader as plugin_loader
from stash_ai_server.core.system_settings import SYSTEM_PLUGIN_NAME, get_value as sys_get_value, invalidate_cache as sys_invalidate_cache
from stash_ai_server.core.runtime import schedule_backend_restart
from stash_ai_server.core.config import settings
from stash_ai_server.core.compat import version_satisfies
from stash_ai_server.utils.path_mutation import invalidate_path_mapping_cache
from stash_ai_server.core.api_key import require_shared_api_key
import logging

router = APIRouter(prefix='/plugins', tags=['plugins'], dependencies=[Depends(require_shared_api_key)])
logger = logging.getLogger(__name__)

# Dependency

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _require_plugin_active(db: Session, plugin_name: str):
    meta = db.execute(select(PluginMeta).where(PluginMeta.name == plugin_name)).scalar_one_or_none()
    if meta and meta.status == 'active':
        return meta
    status = meta.status if meta else 'missing'
    message = (meta.last_error if meta and meta.last_error else 'Plugin did not activate successfully.')
    raise HTTPException(
        status_code=409,
        detail={
            'code': 'PLUGIN_INACTIVE',
            'plugin': plugin_name,
            'status': status,
            'message': message,
        },
    )


def _require_backend_compatibility(plan: plugin_loader.InstallPlanResult):
    backend_version = getattr(settings, 'version', None)
    for target in plan.order:
        required = plan.required_backend.get(target)
        if not required:
            continue
        if version_satisfies(backend_version, required):
            continue
        detected = backend_version or 'unknown'
        raise HTTPException(
            status_code=409,
            detail={
                'code': 'BACKEND_TOO_OLD',
                'plugin': target,
                'required_backend': required,
                'backend_version': detected,
                'message': f"Plugin '{target}' requires backend {required}, but the server is {detected}.",
            },
        )

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

class ReloadRequest(BaseModel):
    plugin: str

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
    return [row for row in rows if row.name != 'local']

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
    if row.name == 'local':
        raise HTTPException(status_code=400, detail='SOURCE_IMMUTABLE')
    db.delete(row)
    db.commit()
    return {'status': 'deleted'}



@router.get('/settings/{plugin_name}', response_model=List[PluginSettingModel])
async def list_plugin_settings(plugin_name: str, db: Session = Depends(get_db)):
    """List stored plugin settings (definitions + current values)."""
    # Ensure deterministic ordering (DB insertion order) so frontends can rely on plugin-defined order
    rows = db.execute(select(PluginSetting).where(PluginSetting.plugin_name == plugin_name).order_by(PluginSetting.id)).scalars().all()
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
    v = normalize_null_strings(payload.value)
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
    # Null means reset to default
    row.value = v
    db.commit()
    if key == 'path_mappings':
        invalidate_path_mapping_cache(plugin_name)
    return {'status': 'ok'}

# ---------------- Tag selection endpoints -----------------
# Note: These endpoints must be defined before /system/settings/{key} to avoid route conflicts

# Model name to display name and category mapping
_MODEL_INFO = {
    'gentler_river': {'display': 'Gentler River', 'category': 'actions', 'category_display': 'Sexual Actions'},
    'stilted_glade': {'display': 'Stilted Glade', 'category': 'bdsm', 'category_display': 'BDSM'},
    'fearless_terrain': {'display': 'Fearless Terrain', 'category': 'bodyparts', 'category_display': 'Body Parts'},
    'blooming_star': {'display': 'Blooming Star', 'category': 'positions', 'category_display': 'Positions'},
    'vivid_galaxy': {'display': 'Vivid Galaxy (Free)', 'category': 'actions', 'category_display': 'Sexual Actions'},
    'distinctive_haze': {'display': 'Distinctive Haze (VIP)', 'category': 'actions', 'category_display': 'Sexual Actions'},
    'happy_terrain': {'display': 'Happy Terrain (VIP)', 'category': 'bdsm', 'category_display': 'BDSM'},
    'electric_smoke': {'display': 'Electric Smoke (VIP)', 'category': 'bodyparts', 'category_display': 'Body Parts'},
}

# Tag lists by category (from CSV)
_TAGS_BY_CATEGORY = {
    'actions': [
        '69', 'Anal Fucking', 'Ass Licking', 'Ass Penetration', 'Ball Licking/Sucking', 'Blowjob',
        'Cum on Person', 'Cum Swapping', 'Cumshot', 'Deepthroat', 'Double Penetration', 'Fingering',
        'Fisting', 'Footjob', 'Gangbang', 'Gloryhole', 'Grabbing Ass', 'Grabbing Boobs',
        'Grabbing Hair/Head', 'Handjob', 'Kissing', 'Licking Penis', 'Masturbation', 'Pissing',
        'Pussy Licking (Clearly Visible)', 'Pussy Licking', 'Pussy Rubbing', 'Sucking Fingers',
        'Sucking Toy/Dildo', 'Wet (Genitals)', 'Titjob', 'Tribbing/Scissoring', 'Undressing',
        'Vaginal Penetration', 'Vaginal Fucking', 'Vibrating'
    ],
    'bdsm': [
        'Chastity', 'Female Bondage', 'Male Bondage', 'Bondage', 'Choking', 'Pegging',
        'Nipple Clamps', 'Gag', 'Pain', 'Anal Hook', 'Chastity (Male)', 'Chastity (Female)',
        'Metal Chastity', 'Plastic Chastity', 'Cum in Chastity', 'Crotch Roped', 'Bondaged Boobs',
        'Tied Penis', 'Tied Balls', 'Clover Clamps', 'Clothes Pin', 'Weights', 'Alligator Clamp',
        'Ball Gag', 'Ring Gag', 'Harness Gag', 'Bit Gag', 'Muzzle Gag', 'Dildo Gag',
        'Inflatable Gag', 'Tape Gag', 'Rope Bondage', 'Metal Bondage', 'Leather Bondage',
        'Collared', 'Blindfolded', 'Chair Tied', 'Straight Jacket', 'Yoke', 'Whip', 'Flogger',
        'Electric Torture', 'Crush Torture', 'Arm Binder', 'Rope Collar', 'Leather Collar',
        'Metal Collar', 'Leash', 'Catheter', 'Handcuffed'
    ],
    'bodyparts': [
        'Ass', 'Asshole', 'Anal Gape', 'Balls', 'Boobs', 'Cum', 'Dick', 'Face', 'Feet',
        'Fingers', 'Belly Button', 'Nipples', 'Thighs', 'Lower Legs', 'Tongue', 'Pussy',
        'Pussy Gape', 'Spit', 'Oiled', 'Wet (Water)', 'Pussy Fully Visible', 'Pussy Closeup',
        'Pussy Very Closeup', 'Wet Pussy', 'Very Wet Pussy', 'Cum on Pussy', 'Small Labia',
        'Big Labia', 'Pierced Pussy', 'Pussy Hair', 'Very Hairy Pussy', 'Innie', 'Medium Labia',
        'Spread Labia', 'Pink Pussy', 'Brown Pussy', 'Shaved Pussy'
    ],
    'positions': [
        '69_Position', 'Doggystyle', 'Facesitting', 'Cowgirl', 'Rev Cowgirl', 'Missionary',
        'G Missionary', 'Stand Cradle', 'Kneeling', 'Laying Down', 'Bent Over', 'Sitting',
        'Squatting', 'Flexible', 'Upskirt', 'FPOV', 'MPOV', 'CloseUp', '1F', '1F1M', '2F',
        '2F1M', '3F', '1F2M', '1M', '2M', '3M', 'Orgy'
    ],
}

# Free model has reduced tag list (only 10 tags)
_FREE_MODEL_TAGS = [
    '69', 'Anal Fucking', 'Blowjob', 'Cumshot', 'Fingering', 'Handjob', 'Kissing',
    'Pussy Licking', 'Pussy Rubbing', 'Vaginal Fucking'
]


async def _get_ai_server_url(db: Session) -> str | None:
    """Get AI server URL from skier_aitagging plugin settings."""
    row = db.execute(
        select(PluginSetting).where(
            PluginSetting.plugin_name == 'skier_aitagging',
            PluginSetting.key == 'server_url'
        )
    ).scalar_one_or_none()
    if row:
        return row.value if row.value is not None else row.default_value
    return None


async def _get_active_models(ai_server_url: str) -> List[Dict[str, Any]]:
    """Query AI server for active models."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{ai_server_url.rstrip('/')}/v3/current_ai_models/")
            response.raise_for_status()
            return response.json()
    except Exception:
        return []


async def _get_tags_from_server(ai_server_url: str) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Query AI server for available tags and model information.
    
    Returns:
        Tuple of (all_tags_list, models_data_list)
    """
    import logging
    logger = logging.getLogger(__name__)
    url = f"{ai_server_url.rstrip('/')}/tags/available"
    logger.info(f"Fetching tags from AI server: {url}")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            logger.info(f"AI server response status: {response.status_code}")
            response.raise_for_status()
            data = response.json()
            tag_count = len(data.get('tags', []))
            model_count = len(data.get('models', []))
            logger.info(f"AI server returned {tag_count} tags and {model_count} models")
            all_tags = data.get('tags', [])
            models_data = data.get('models', [])
            return all_tags, models_data
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error fetching tags from AI server {url}: {e.response.status_code} - {e.response.text}")
        return [], []
    except Exception as e:
        logger.error(f"Failed to fetch tags from AI server {url}: {e}", exc_info=True)
        return [], []


class AvailableTagsResponse(BaseModel):
    tags: List[Dict[str, Any]]
    models: List[Dict[str, Any]]
    error: Optional[str] = None

class ExcludedTagsResponse(BaseModel):
    excluded_tags: List[str]


@router.get('/system/tags/test', name='test_tags_endpoint')
async def test_tags_endpoint():
    """Test endpoint to verify router is working."""
    return {"status": "ok", "message": "Tags router is working!"}

@router.get('/system/tags/available', response_model=AvailableTagsResponse, name='get_available_tags')
async def get_available_tags(db: Session = Depends(get_db)):
    """Get list of available tags from active AI models."""
    import logging
    logger = logging.getLogger(__name__)
    ai_server_url = await _get_ai_server_url(db)
    logger.info(f"AI server URL from config: {ai_server_url}")
    if not ai_server_url:
        logger.warning("AI server URL not configured in database")
        return AvailableTagsResponse(tags=[], models=[], error='AI server URL not configured')
    
    # Fetch tags and model data directly from the model server
    server_tags, server_models_data = await _get_tags_from_server(ai_server_url)
    
    if not server_models_data:
        logger.warning("No model data returned from AI server, falling back to model info")
        # Fallback: try to get model info and use hardcoded tags
        active_models = await _get_active_models(ai_server_url)
        if not active_models:
            return AvailableTagsResponse(tags=[], models=[], error='Could not fetch tags or models from AI server')
        
        # Use fallback logic with hardcoded tags
        tags_list = []
        models_list = []
        seen_tags = set()
        
        for model_info in active_models:
            model_name = model_info.get('name', model_info.get('model_file_name', '')).replace('.yaml', '').replace('.pt', '').replace('.pt.enc', '')
            if not model_name:
                continue
            
            model_display_info = _MODEL_INFO.get(model_name)
            if not model_display_info:
                category = model_info.get('categories', model_info.get('model_category', []))
                if category and isinstance(category, list) and len(category) > 0:
                    cat = category[0]
                    model_display_info = {
                        'display': model_name.replace('_', ' ').title(),
                        'category': cat,
                        'category_display': cat.replace('actions', 'Sexual Actions').replace('bdsm', 'BDSM').replace('bodyparts', 'Body Parts').title()
                    }
                else:
                    continue
            
            model_display_name = model_display_info['display']
            category = model_display_info['category']
            category_display = model_display_info['category_display']
            
            # Fallback to hardcoded tags
            category_tags = _TAGS_BY_CATEGORY.get(category, [])
            if model_name == 'vivid_galaxy':
                category_tags = _FREE_MODEL_TAGS
            
            models_list.append({
                'name': model_name,
                'displayName': model_display_name,
                'category': category,
                'categoryDisplay': category_display,
                'tagCount': len(category_tags)
            })
            
            # Add tags for this model
            for tag in category_tags:
                tag_key = f"{tag}::{model_name}"
                if tag_key not in seen_tags:
                    seen_tags.add(tag_key)
                    tags_list.append({
                        'tag': tag,
                        'model': model_name,
                        'modelDisplayName': model_display_name,
                        'category': category,
                        'categoryDisplay': category_display
                    })
        
        return AvailableTagsResponse(tags=tags_list, models=models_list)
    
    # Use model-specific tag data from server
    tags_list = []
    models_list = []
    seen_tags = set()
    
    for model_data in server_models_data:
        model_name = model_data.get('name', '').replace('.yaml', '').replace('.pt', '').replace('.pt.enc', '')
        if not model_name:
            continue
        
        model_tags = model_data.get('tags', [])
        model_categories = model_data.get('categories', [])
        
        # Get display info for the model
        model_display_info = _MODEL_INFO.get(model_name)
        if not model_display_info:
            # Try to infer from model categories
            if model_categories and isinstance(model_categories, list) and len(model_categories) > 0:
                cat = model_categories[0]
                model_display_info = {
                    'display': model_name.replace('_', ' ').title(),
                    'category': cat,
                    'category_display': cat.replace('actions', 'Sexual Actions').replace('bdsm', 'BDSM').replace('bodyparts', 'Body Parts').replace('positions', 'Positions').title()
                }
            else:
                # Skip if we can't determine category
                continue
        
        model_display_name = model_display_info['display']
        category = model_display_info.get('category', model_categories[0] if model_categories else 'unknown')
        category_display = model_display_info['category_display']
        
        # Add model to models list
        models_list.append({
            'name': model_name,
            'displayName': model_display_name,
            'category': category,
            'categoryDisplay': category_display,
            'tagCount': len(model_tags)
        })
        
        # Add tags for this specific model
        for tag in model_tags:
            tag_key = f"{tag}::{model_name}"
            if tag_key not in seen_tags:
                seen_tags.add(tag_key)
                tags_list.append({
                    'tag': tag,
                    'model': model_name,
                    'modelDisplayName': model_display_name,
                    'category': category,
                    'categoryDisplay': category_display
                })
    
    return AvailableTagsResponse(tags=tags_list, models=models_list)


@router.get('/system/tags/excluded', response_model=ExcludedTagsResponse, name='get_excluded_tags')
async def get_excluded_tags(db: Session = Depends(get_db)):
    """Get current list of excluded tags."""
    import logging
    logger = logging.getLogger(__name__)
    logger.info("get_excluded_tags endpoint called")
    excluded = sys_get_value('EXCLUDED_TAGS', [])
    if excluded is None:
        excluded = []
    if isinstance(excluded, str):
        try:
            excluded = json.loads(excluded)
        except:
            excluded = []
    return ExcludedTagsResponse(excluded_tags=excluded if isinstance(excluded, list) else [])


# ---------------- System (global) settings endpoints -----------------

@router.get('/system/settings', response_model=List[PluginSettingModel])
async def list_system_settings(db: Session = Depends(get_db)):
    # Keep system settings in deterministic order as well
    rows = db.execute(select(PluginSetting).where(PluginSetting.plugin_name == SYSTEM_PLUGIN_NAME).order_by(PluginSetting.id)).scalars().all()
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
    logger.info("upsert_system_setting: Received request to update setting key=%s", key)
    row = db.execute(select(PluginSetting).where(PluginSetting.plugin_name == SYSTEM_PLUGIN_NAME, PluginSetting.key == key)).scalar_one_or_none()
    if not row:
        logger.warning("upsert_system_setting: Setting not found - key=%s", key)
        raise HTTPException(status_code=404, detail='NOT_FOUND')
    v = normalize_null_strings(payload.value)
    previous_effective = row.value if row.value is not None else row.default_value
    
    # Special logging for EXCLUDED_TAGS
    if key == 'EXCLUDED_TAGS':
        logger.info(
            "upsert_system_setting: Updating EXCLUDED_TAGS - previous value: %s, new value: %s",
            previous_effective,
            v
        )
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
        elif row.type == 'json':
            if not isinstance(v, (list, dict, str, int, float, bool, type(None))):
                raise HTTPException(status_code=400, detail='INVALID_JSON')
            if isinstance(v, str):
                try:
                    v = json.loads(v)
                except json.JSONDecodeError:
                    raise HTTPException(status_code=400, detail='INVALID_JSON')
    row.value = v
    db.commit()
    sys_invalidate_cache()
    if key == 'PATH_MAPPINGS':
        invalidate_path_mapping_cache(system=True)
    current_effective = row.value if row.value is not None else row.default_value
    
    # Special logging for EXCLUDED_TAGS after save
    if key == 'EXCLUDED_TAGS':
        logger.info(
            "upsert_system_setting: Successfully saved EXCLUDED_TAGS - saved value: %s (type: %s)",
            current_effective,
            type(current_effective).__name__
        )
        # Verify it can be retrieved
        from stash_ai_server.core.system_settings import get_value as sys_get_value
        retrieved = sys_get_value('EXCLUDED_TAGS', [])
        logger.info(
            "upsert_system_setting: Verified EXCLUDED_TAGS retrieval after save - retrieved: %s (type: %s)",
            retrieved,
            type(retrieved).__name__
        )
    
    if key in {'STASH_URL', 'STASH_API_KEY'} and current_effective != previous_effective:
        schedule_backend_restart()
    logger.info("upsert_system_setting: Successfully updated setting key=%s", key)
    return {'status': 'ok'}
_TAGS_BY_CATEGORY = {
    'actions': [
        '69', 'Anal Fucking', 'Ass Licking', 'Ass Penetration', 'Ball Licking/Sucking', 'Blowjob',
        'Cum on Person', 'Cum Swapping', 'Cumshot', 'Deepthroat', 'Double Penetration', 'Fingering',
        'Fisting', 'Footjob', 'Gangbang', 'Gloryhole', 'Grabbing Ass', 'Grabbing Boobs',
        'Grabbing Hair/Head', 'Handjob', 'Kissing', 'Licking Penis', 'Masturbation', 'Pissing',
        'Pussy Licking (Clearly Visible)', 'Pussy Licking', 'Pussy Rubbing', 'Sucking Fingers',
        'Sucking Toy/Dildo', 'Wet (Genitals)', 'Titjob', 'Tribbing/Scissoring', 'Undressing',
        'Vaginal Penetration', 'Vaginal Fucking', 'Vibrating'
    ],
    'bdsm': [
        'Chastity', 'Female Bondage', 'Male Bondage', 'Bondage', 'Choking', 'Pegging',
        'Nipple Clamps', 'Gag', 'Pain', 'Anal Hook', 'Chastity (Male)', 'Chastity (Female)',
        'Metal Chastity', 'Plastic Chastity', 'Cum in Chastity', 'Crotch Roped', 'Bondaged Boobs',
        'Tied Penis', 'Tied Balls', 'Clover Clamps', 'Clothes Pin', 'Weights', 'Alligator Clamp',
        'Ball Gag', 'Ring Gag', 'Harness Gag', 'Bit Gag', 'Muzzle Gag', 'Dildo Gag',
        'Inflatable Gag', 'Tape Gag', 'Rope Bondage', 'Metal Bondage', 'Leather Bondage',
        'Collared', 'Blindfolded', 'Chair Tied', 'Straight Jacket', 'Yoke', 'Whip', 'Flogger',
        'Electric Torture', 'Crush Torture', 'Arm Binder', 'Rope Collar', 'Leather Collar',
        'Metal Collar', 'Leash', 'Catheter', 'Handcuffed'
    ],
    'bodyparts': [
        'Ass', 'Asshole', 'Anal Gape', 'Balls', 'Boobs', 'Cum', 'Dick', 'Face', 'Feet',
        'Fingers', 'Belly Button', 'Nipples', 'Thighs', 'Lower Legs', 'Tongue', 'Pussy',
        'Pussy Gape', 'Spit', 'Oiled', 'Wet (Water)', 'Pussy Fully Visible', 'Pussy Closeup',
        'Pussy Very Closeup', 'Wet Pussy', 'Very Wet Pussy', 'Cum on Pussy', 'Small Labia',
        'Big Labia', 'Pierced Pussy', 'Pussy Hair', 'Very Hairy Pussy', 'Innie', 'Medium Labia',
        'Spread Labia', 'Pink Pussy', 'Brown Pussy', 'Shaved Pussy'
    ],
    'positions': [
        '69_Position', 'Doggystyle', 'Facesitting', 'Cowgirl', 'Rev Cowgirl', 'Missionary',
        'G Missionary', 'Stand Cradle', 'Kneeling', 'Laying Down', 'Bent Over', 'Sitting',
        'Squatting', 'Flexible', 'Upskirt', 'FPOV', 'MPOV', 'CloseUp', '1F', '1F1M', '2F',
        '2F1M', '3F', '1F2M', '1M', '2M', '3M', 'Orgy'
    ],
}

# Free model has reduced tag list (only 10 tags)
_FREE_MODEL_TAGS = [
    '69', 'Anal Fucking', 'Blowjob', 'Cumshot', 'Fingering', 'Handjob', 'Kissing',
    'Pussy Licking', 'Pussy Rubbing', 'Vaginal Fucking'
]


async def _get_ai_server_url(db: Session) -> str | None:
    """Get AI server URL from skier_aitagging plugin settings."""
    row = db.execute(
        select(PluginSetting).where(
            PluginSetting.plugin_name == 'skier_aitagging',
            PluginSetting.key == 'server_url'
        )
    ).scalar_one_or_none()
    if row:
        return row.value if row.value is not None else row.default_value
    return None


async def _get_active_models(ai_server_url: str) -> List[Dict[str, Any]]:
    """Query AI server for active models."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{ai_server_url.rstrip('/')}/v3/current_ai_models/")
            response.raise_for_status()
            return response.json()
    except Exception:
        return []


class AvailableTagsResponse(BaseModel):
    tags: List[Dict[str, Any]]
    models: List[Dict[str, Any]]
    error: Optional[str] = None

INDEX_EXPECTED_SCHEMA = 1

@router.post('/sources/{source_name}/refresh', response_model=RefreshResult)
async def refresh_source(source_name: str, db: Session = Depends(get_db)):
    src = db.execute(select(PluginSource).where(PluginSource.name == source_name)).scalar_one_or_none()
    if not src:
        raise HTTPException(status_code=404, detail='NOT_FOUND')
    if not src.enabled:
        raise HTTPException(status_code=400, detail='SOURCE_DISABLED')
    if src.name == 'local':
        raise HTTPException(status_code=400, detail='SOURCE_IMMUTABLE')

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
                        raw_dep = entry.get('dependsOn')
                        if raw_dep is None:
                            raw_dep = entry.get('depends_on')
                        dep_normalized = normalize_null_strings(raw_dep)
                        dependencies: list[str] = []
                        if isinstance(dep_normalized, (list, tuple, set)):
                            for dep in dep_normalized:
                                if dep is None:
                                    continue
                                dep_text = str(dep).strip()
                                if not dep_text or dep_text.lower() in ('null', 'none'):
                                    continue
                                dependencies.append(dep_text)
                        elif dep_normalized:
                            dep_text = str(dep_normalized).strip()
                            if dep_text and dep_text.lower() not in ('null', 'none'):
                                dependencies.append(dep_text)
                        manifest_copy = normalize_null_strings(entry)
                        catalog = PluginCatalog(
                            source_id=src.id,
                            plugin_name=plugin_name,
                            version=str(entry.get('version', '0.0.0')),
                            description=entry.get('description'),
                            human_name=entry.get('humanName') or entry.get('human_name'),
                            server_link=entry.get('serverLink') or entry.get('server_link'),
                            dependencies_json={'plugins': dependencies} if dependencies else None,
                            manifest_json=manifest_copy if isinstance(manifest_copy, dict) else None,
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
    _require_backend_compatibility(plan)
    deps_to_install = [name for name in plan.dependencies if name not in plan.already_active]
    if deps_to_install and not install_dependencies:
        raise HTTPException(status_code=409, detail={'code': 'DEPENDENCIES_REQUIRED', 'dependencies': deps_to_install, 'human_names': {n: plan.human_names.get(n) for n in deps_to_install}})
    try:
        installed = plugin_loader.execute_install_plan(db, plan, overwrite_target=overwrite, install_dependencies=install_dependencies or bool(deps_to_install))
        primary_version = next((ver for name, ver in installed if name == plugin_name), None)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    meta = _require_plugin_active(db, plugin_name)
    return {'status': 'installed', 'plugin': plugin_name, 'version': primary_version or meta.version, 'installed': installed}


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
    _require_backend_compatibility(plan)
    try:
        installed = plugin_loader.execute_install_plan(db, plan, overwrite_target=True, install_dependencies=True)
        primary_version = next((ver for name, ver in installed if name == plugin_name), None)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    meta = _require_plugin_active(db, plugin_name)
    return {'status': 'updated', 'plugin': plugin_name, 'version': primary_version or meta.version, 'installed': installed}


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

@router.post('/reload', response_model=PluginMetaModel)
async def reload_plugin_endpoint(payload: ReloadRequest, db: Session = Depends(get_db)):
    plugin_name = (payload.plugin or '').strip()
    if not plugin_name:
        raise HTTPException(status_code=400, detail='PLUGIN_REQUIRED')
    try:
        meta = plugin_loader.reload_plugin(db, plugin_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail='PLUGIN_NOT_FOUND')
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail='RELOAD_FAILED') from exc
    return meta

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
