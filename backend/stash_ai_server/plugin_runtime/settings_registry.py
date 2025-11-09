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
from stash_ai_server.utils.string_utils import normalize_null_strings

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
        default = normalize_null_strings(d.get('default'))
        desc = d.get('description') or d.get('help')
        options = normalize_null_strings(d.get('options'))
        row = by_key.get(key)
        if not row:
            row = PluginSetting(
                plugin_name=plugin_name,
                key=key,
                type=t,
                label=label,
                description=desc,
                default_value=default,
                options=options,
                value=default,
            )
            db.add(row); by_key[key] = row; changed = True
        else:
            meta_changed = False
            if row.type != t: row.type = t; meta_changed = True
            if (row.label or '') != (label or ''): row.label = label; meta_changed = True
            if (row.description or '') != (desc or ''): row.description = desc; meta_changed = True
            if row.default_value != default: row.default_value = default; meta_changed = True
            if row.options != options: row.options = options; meta_changed = True
            if row.value is None and default is not None:
                row.value = default
                meta_changed = True
            if meta_changed: changed = True
    if changed:
        db.commit()


def load_plugin_settings(plugin_name: str) -> dict[str, Any]:
    """Load current plugin settings from the database (value fallback to default).

    This mirrors the logic used by service registration so plugins without a
    dedicated service class can still consume their stored settings during
    module import or initialization.
    """
    try:
        from stash_ai_server.db.session import SessionLocal
    except Exception:
        return {}

    session = None
    try:
        session = SessionLocal()
    except Exception:
        return {}

    try:
        rows = (
            session.execute(
                select(PluginSetting).where(PluginSetting.plugin_name == plugin_name)
            ).scalars().all()
        )
        resolved: Dict[str, Any] = {}
        for row in rows:
            value = row.value if row.value is not None else row.default_value
            if value is None:
                continue
            resolved[row.key] = value
        return resolved
    except Exception:
        return {}
    finally:
        try:
            if session is not None:
                session.close()
        except Exception:
            pass
