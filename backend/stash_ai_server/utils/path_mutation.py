from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Mapping, Sequence

from sqlalchemy import select

from stash_ai_server.db.session import SessionLocal
from stash_ai_server.models.plugin import PluginSetting

SYSTEM_PLUGIN_NAME = "__system__"

_log = logging.getLogger(__name__)

@dataclass(frozen=True, slots=True)
class PathMapping:
    source: str
    target: str
    slash_mode: str

_DEFAULT_MODE = "auto"
_SUPPORTED_MODES = {"auto", "unix", "win", "windows", "unchanged", "keep"}
_PATH_CACHE_LOCK = threading.Lock()
_SYSTEM_CACHE: tuple[tuple[PathMapping, ...], int] | None = None
_PLUGIN_CACHE: dict[str, tuple[tuple[PathMapping, ...], int]] = {}
_CACHE_GEN = 0


def _normalize_mode(raw: str | None) -> str:
    if not raw:
        return _DEFAULT_MODE
    mode = raw.strip().lower()
    if mode == "windows":
        mode = "win"
    if mode not in _SUPPORTED_MODES:
        return _DEFAULT_MODE
    if mode == "keep":
        return "unchanged"
    return mode


def _coerce_mapping(item: Mapping[str, object]) -> PathMapping | None:
    source_val = (item.get("source") or item.get("source_path") or "").strip()
    target_val = (item.get("target") or item.get("target_path") or "").strip()
    if not source_val:
        return None
    mode_val = _normalize_mode(item.get("slash_mode") if isinstance(item.get("slash_mode"), str) else None)
    return PathMapping(source=source_val, target=target_val, slash_mode=mode_val)


def _coerce_mappings(raw: object) -> tuple[PathMapping, ...]:
    if raw is None:
        return ()
    parsed: object = raw
    if isinstance(parsed, Sequence):
        mappings: list[PathMapping] = []
        for item in parsed:
            if isinstance(item, Mapping):
                mapping = _coerce_mapping(item)
                if mapping:
                    mappings.append(mapping)
            elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
                try:
                    source_val = str(item[0])
                    target_val = str(item[1]) if len(item) > 1 else ""
                    mode_val = str(item[2]) if len(item) > 2 else None
                except Exception:
                    continue
                mapping = _coerce_mapping({"source": source_val, "target": target_val, "slash_mode": mode_val})
                if mapping:
                    mappings.append(mapping)
        # Sort longest source first to prioritize specific prefixes
        mappings.sort(key=lambda m: len(m.source), reverse=True)
        return tuple(mappings)
    return ()


def _fetch_setting(plugin_name: str, key: str) -> object | None:
    with SessionLocal() as session:
        row = (
            session.execute(
                select(PluginSetting).where(
                    PluginSetting.plugin_name == plugin_name,
                    PluginSetting.key == key,
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            return None
        return row.value if row.value is not None else row.default_value


def _load_system_mappings() -> tuple[PathMapping, ...]:
    value = _fetch_setting(SYSTEM_PLUGIN_NAME, "PATH_MAPPINGS")
    return _coerce_mappings(value)


def _load_plugin_mappings(plugin_name: str) -> tuple[PathMapping, ...]:
    value = _fetch_setting(plugin_name, "path_mappings")
    return _coerce_mappings(value)


def invalidate_path_mapping_cache(plugin_name: str | None = None, *, system: bool = False) -> None:
    global _SYSTEM_CACHE, _PLUGIN_CACHE, _CACHE_GEN
    with _PATH_CACHE_LOCK:
        if plugin_name is None and not system:
            _PLUGIN_CACHE.clear()
            _SYSTEM_CACHE = None
            _CACHE_GEN += 1
            return
        if system:
            _SYSTEM_CACHE = None
        if plugin_name:
            _PLUGIN_CACHE.pop(plugin_name, None)
        _CACHE_GEN += 1


def _get_system_mappings() -> tuple[PathMapping, ...]:
    global _SYSTEM_CACHE
    with _PATH_CACHE_LOCK:
        cached = _SYSTEM_CACHE
    if cached is not None:
        return cached[0]
    mappings = _load_system_mappings()
    with _PATH_CACHE_LOCK:
        _SYSTEM_CACHE = (mappings, _CACHE_GEN)
    return mappings


def _get_plugin_mappings(plugin_name: str | None) -> tuple[PathMapping, ...]:
    if not plugin_name:
        return ()
    with _PATH_CACHE_LOCK:
        cached = _PLUGIN_CACHE.get(plugin_name)
    if cached is not None:
        return cached[0]
    mappings = _load_plugin_mappings(plugin_name)
    with _PATH_CACHE_LOCK:
        _PLUGIN_CACHE[plugin_name] = (mappings, _CACHE_GEN)
    return mappings


def _looks_like_windows_path(value: str) -> bool:
    if not value:
        return False
    stripped = value.lstrip()
    if not stripped:
        return False
    if len(stripped) >= 2 and stripped[1] == ":" and stripped[0].isalpha():
        return True
    if stripped.startswith("\\"):
        return True
    first_forward = stripped.find("/")
    first_backward = stripped.find("\\")
    if first_backward == -1:
        return False
    if first_forward == -1:
        return True
    return first_backward < first_forward


def _should_ignore_case(pattern: str) -> bool:
    return _looks_like_windows_path(pattern)


def _normalize_slashes(value: str, mode: str) -> str:
    if not value:
        return value
    if mode == "unchanged":
        return value
    if mode == "win":
        return value.replace("/", "\\")
    if mode == "unix":
        replaced = value.replace("\\", "/")
        if replaced.startswith("//"):
            return replaced
        if not replaced.startswith("/"):
            replaced = "/" + replaced.lstrip("/")
        return replaced
    if mode == "auto":
        if _looks_like_windows_path(value):
            return value.replace("/", "\\")
        replaced = value.replace("\\", "/")
        if replaced.startswith("//"):
            return replaced
        if not replaced.startswith("/"):
            replaced = "/" + replaced.lstrip("/")
        return replaced
    return value


def _apply_mappings(original: str, mappings: Sequence[PathMapping]) -> tuple[str, PathMapping | None]:
    if not original:
        return original, None
    chosen: PathMapping | None = None
    chosen_index = -1
    lower_original: str | None = None
    for mapping in mappings:
        source = mapping.source
        if not source:
            continue
        if _should_ignore_case(source):
            if lower_original is None:
                lower_original = original.lower()
            idx = lower_original.find(source.lower())
        else:
            idx = original.find(source)
        if idx != -1:
            chosen = mapping
            chosen_index = idx
            break
    if chosen is None:
        return original, None
    end_index = chosen_index + len(chosen.source)
    replaced = f"{original[:chosen_index]}{chosen.target}{original[end_index:]}"
    normalized = _normalize_slashes(replaced, chosen.slash_mode)
    return normalized or original, chosen


def mutate_path_for_plugin(path: str, plugin_name: str | None) -> str:
    if not path:
        return path
    plugin_path, _ = _apply_mappings(path, _get_plugin_mappings(plugin_name))
    return plugin_path or path


def mutate_path_for_backend(path: str) -> str:
    if not path:
        return path
    ai_path, _ = _apply_mappings(path, _get_system_mappings())
    return ai_path or path


