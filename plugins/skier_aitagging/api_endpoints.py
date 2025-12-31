"""
API endpoints for Skier AI Tagging plugin tag list editor.
These endpoints need to be added to stash_ai_server/api/plugins.py

Add these endpoints after the existing plugin settings endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel
from typing import List, Optional, Any, Dict
from sqlalchemy.orm import Session
from sqlalchemy import select
from stash_ai_server.db.session import SessionLocal
from stash_ai_server.models.plugin import PluginMeta
from stash_ai_server.services import registry as services_registry

# These should be added to the existing router in plugins.py
# router = APIRouter(prefix='/plugins', tags=['plugins'], dependencies=[Depends(require_shared_api_key)])

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

class TagStatusUpdate(BaseModel):
    tag_statuses: Optional[Dict[str, bool]] = None
    enabled_tags: Optional[List[str]] = None
    disabled_tags: Optional[List[str]] = None

# Add these endpoints to plugins.py router:

@router.get('/settings/{plugin_name}/tags/available')
async def get_plugin_available_tags(plugin_name: str, db: Session = Depends(get_db)):
    """Get available tags for a plugin that supports tag editing."""
    _require_plugin_active(db, plugin_name)
    
    # Try to find the plugin's service
    service = None
    for svc in services_registry.services.list():
        if getattr(svc, 'plugin_name', None) == plugin_name:
            service = svc
            break
    
    if not service:
        raise HTTPException(status_code=404, detail='PLUGIN_SERVICE_NOT_FOUND')
    
    # Check if service has the method
    if not hasattr(service, 'get_available_tags_data'):
        raise HTTPException(status_code=400, detail='PLUGIN_DOES_NOT_SUPPORT_TAG_EDITING')
    
    try:
        # Get ALL tags (including disabled ones) for the editor
        result = await service.get_available_tags_data(include_disabled=True)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@router.get('/settings/{plugin_name}/tags/statuses')
async def get_plugin_tag_statuses(plugin_name: str, db: Session = Depends(get_db)):
    """Get all tag enabled statuses for a plugin."""
    _require_plugin_active(db, plugin_name)
    
    service = None
    for svc in services_registry.services.list():
        if getattr(svc, 'plugin_name', None) == plugin_name:
            service = svc
            break
    
    if not service:
        raise HTTPException(status_code=404, detail='PLUGIN_SERVICE_NOT_FOUND')
    
    if not hasattr(service, 'get_all_tag_statuses'):
        raise HTTPException(status_code=400, detail='PLUGIN_DOES_NOT_SUPPORT_TAG_EDITING')
    
    try:
        # get_all_tag_statuses is now async
        result = await service.get_all_tag_statuses()
        return {'statuses': result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@router.put('/settings/{plugin_name}/tags/statuses')
async def update_plugin_tag_statuses(plugin_name: str, payload: TagStatusUpdate, db: Session = Depends(get_db)):
    """Update tag enabled statuses for a plugin."""
    _require_plugin_active(db, plugin_name)
    
    service = None
    for svc in services_registry.services.list():
        if getattr(svc, 'plugin_name', None) == plugin_name:
            service = svc
            break
    
    if not service:
        raise HTTPException(status_code=404, detail='PLUGIN_SERVICE_NOT_FOUND')
    
    if not hasattr(service, 'update_tag_enabled_status'):
        raise HTTPException(status_code=400, detail='PLUGIN_DOES_NOT_SUPPORT_TAG_EDITING')
    
    try:
        result = service.update_tag_enabled_status(
            tag_statuses=payload.tag_statuses,
            enabled_tags=payload.enabled_tags,
            disabled_tags=payload.disabled_tags
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
