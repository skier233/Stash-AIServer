from __future__ import annotations
"""System (global) configuration stored in the same table as plugin settings.

We reuse PluginSetting with plugin_name='__system__'. This lets us leverage the
existing API patterns and UI input rendering logic while keeping a single
storage model.
"""
from typing import Any, Dict, List
import os
from sqlalchemy import select
from sqlalchemy.orm import Session
from stash_ai_server.db.session import SessionLocal
from stash_ai_server.models.plugin import PluginSetting
from stash_ai_server.utils.string_utils import normalize_null_strings

SYSTEM_PLUGIN_NAME = '__system__'

_DEFS: List[Dict[str, Any]] = [
    { 'key': 'STASH_URL', 'type': 'string', 'label': 'Stash URL', 'default': 'http://localhost:9999', 'description': 'Base URL of the Stash instance.' },
    { 'key': 'STASH_API_KEY', 'type': 'string', 'label': 'Stash API Key', 'default': 'REPLACE_WITH_API_KEY', 'description': 'API key used to connect to Stash.' },
    { 'key': 'STASH_DB_PATH', 'type': 'string', 'label': 'Stash DB Path', 'default': 'REPLACE_WITH_DB_PATH', 'description': 'Path to the Stash database.' },
    { 'key': 'PATH_MAPPINGS', 'type': 'path_map', 'label': 'AI Overhaul Path Mappings', 'default': [], 'description': 'Rewrite stash file paths for the AI Overhaul backend when accessing media directly.' },
    { 'key': 'INTERACTION_MIN_SESSION_MINUTES', 'type': 'number', 'label': 'Interaction Min Session (min)', 'description': 'Minimum session duration in minutes for determining a derived o_count', 'default': 10 },
    { 'key': 'INTERACTION_MERGE_TTL_SECONDS', 'type': 'number', 'label': 'Interaction Merge sessions TTL (s)', 'description': 'Time to merge sessions together if they occur less than this amount of seconds apart', 'default': 120 },
    { 'key': 'SEGMENT_MERGE_GAP_SECONDS', 'type': 'number', 'label': 'Segment Merge Gap (s)', 'description': 'Merges interaction watch segments within this amount of seconds', 'default': 0.5 },
    { 'key': 'INTERACTION_SEGMENT_TIME_MARGIN_SECONDS', 'type': 'number', 'label': 'Interaction Segment Time Margin (s)', 'default': 2 },
    { 'key': 'TASK_LOOP_INTERVAL', 'type': 'number', 'label': 'Task Loop Interval (s)', 'default': 0.5, 'description': 'Scheduler main loop sleep interval.' },
    { 'key': 'TASK_DEBUG', 'type': 'boolean', 'label': 'Task Debug Logging', 'default': False, 'description': 'Verbose task scheduler logging.' },
]

_CACHE: Dict[str, Any] = {}
_CACHE_LOADED = False

def _coerce_value(setting_type: str, value: Any):
    if value is None:
        return None
    try:
        if setting_type == 'number':
            return float(value)
        if setting_type == 'boolean':
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            if isinstance(value, str):
                return value.lower() in ('1','true','yes','on')
            return False
    except Exception:
        return value
    return value

def seed_system_settings():
    """Ensure definition rows exist. If environment variables provide values, use them as initial explicit value (not just default)."""
    db = SessionLocal()
    try:
        existing = { (r.key): r for r in db.execute(select(PluginSetting).where(PluginSetting.plugin_name == SYSTEM_PLUGIN_NAME)).scalars().all() }
        changed = False
        for d in _DEFS:
            key = d['key']
            row = existing.get(key)
            env_val = normalize_null_strings(os.getenv(key))
            default_val = normalize_null_strings(d.get('default'))
            options_val = normalize_null_strings(d.get('options'))
            if row is None:
                # establish row
                val = None
                if env_val is not None:
                    val = _coerce_value(d['type'], env_val)
                row = PluginSetting(
                    plugin_name=SYSTEM_PLUGIN_NAME,
                    key=key,
                    type=d.get('type','string'),
                    label=d.get('label') or key,
                    description=d.get('description'),
                    default_value=default_val,
                    options=options_val,
                    value=(val if val is not None else default_val),
                )
                db.add(row)
                changed = True
            else:
                # update metadata if definition changed
                meta_changed = False
                if row.type != d.get('type','string'): row.type = d.get('type','string'); meta_changed = True
                label = d.get('label') or key
                if (row.label or '') != label: row.label = label; meta_changed = True
                desc = d.get('description')
                if (row.description or '') != (desc or ''): row.description = desc; meta_changed = True
                if row.default_value != default_val:
                    row.default_value = default_val
                    meta_changed = True
                if row.options != options_val: row.options = options_val; meta_changed = True
                # If environment provides value and row has no explicit value (value==default), set it.
                if env_val is not None and row.value in (None, row.default_value):
                    row.value = _coerce_value(d.get('type','string'), env_val)
                    meta_changed = True
                if meta_changed: changed = True
        if changed:
            db.commit()
    finally:
        db.close()
    # Reset cache so subsequent lookups include new values
    global _CACHE, _CACHE_LOADED
    _CACHE.clear(); _CACHE_LOADED = False

def _ensure_cache(db: Session):
    global _CACHE_LOADED
    if _CACHE_LOADED:
        return
    rows = db.execute(select(PluginSetting).where(PluginSetting.plugin_name == SYSTEM_PLUGIN_NAME)).scalars().all()
    for r in rows:
        _CACHE[r.key] = (r.value if r.value is not None else r.default_value)
    _CACHE_LOADED = True

def get_value(key: str, default: Any | None = None) -> Any:
    db = SessionLocal()
    try:
        _ensure_cache(db)
        return _CACHE.get(key, default)
    finally:
        db.close()

def invalidate_cache():
    global _CACHE_LOADED
    _CACHE_LOADED = False
