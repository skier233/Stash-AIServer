from __future__ import annotations
"""Shared settings registration logic for plugin and (soon) system settings.

Future: We may generalize this to handle both plugin-scoped and global
settings rows via a unified table. For now we keep the existing plugin
settings model and reuse the logic.
"""
from typing import Iterable, Mapping, Any, Dict
from sqlalchemy.orm import Session
from sqlalchemy import select
from stash_ai_server.models.plugin import PluginSetting

def register_settings(db: Session, plugin_name: str, definitions: Iterable[Mapping[str, Any]]):
    existing_rows = db.execute(select(PluginSetting).where(PluginSetting.plugin_name == plugin_name)).scalars().all()
    by_key: Dict[str, PluginSetting] = {r.key: r for r in existing_rows}
    changed = False
    for d in definitions:
        key = d.get('key') or d.get('name')
        if not key:
            continue
        t = d.get('type') or 'string'
        label = d.get('label') or key
        default = d.get('default')
        desc = d.get('description') or d.get('help')
        options = d.get('options')
        row = by_key.get(key)
        if not row:
            row = PluginSetting(plugin_name=plugin_name, key=key, type=t, label=label, description=desc, default_value=default, options=options, value=default)
            db.add(row); by_key[key] = row; changed = True
        else:
            meta_changed = False
            if row.type != t: row.type = t; meta_changed = True
            if (row.label or '') != (label or ''): row.label = label; meta_changed = True
            if (row.description or '') != (desc or ''): row.description = desc; meta_changed = True
            if row.default_value != default: row.default_value = default; meta_changed = True
            if row.options != options: row.options = options; meta_changed = True
            if row.value is None and default is not None: row.value = default; meta_changed = True
            if meta_changed: changed = True
    if changed:
        db.commit()
