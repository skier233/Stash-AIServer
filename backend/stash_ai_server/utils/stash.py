from __future__ import annotations
from typing import List, Dict, Any, Optional
import os, traceback
from stash_ai_server.core.system_settings import get_value as sys_get
from urllib.parse import urlparse

_IMPORT_ERR: Optional[str] = None
try:
    # Correct usage pattern aligns with legacy media_handler: stashapi.stashapp
    from stashapi.stashapp import StashInterface, StashVersion  # type: ignore
    print('[stash] stashapi.stashapp import success', flush=True)
except Exception as e:  # pragma: no cover
    _IMPORT_ERR = str(e)
    StashInterface = None  # type: ignore
    StashVersion = None  # type: ignore

# Hardcoded defaults (can be made configurable later). Allow override inside container where 'localhost' would refer to the container itself.
# Priority: STASH_INTERNAL_URL (docker-compose override) > STASH_URL env > default.
_default_url = 'http://localhost:3000'
def _resolve_setting(key: str, fallback: Any) -> Any:
    # Always prefer explicit system setting value; if None, fallback to env; then fallback default.
    val = sys_get(key, None)
    if val is None or val == '':
        return os.getenv(key, fallback)
    return val

STASH_URL = (os.getenv('STASH_INTERNAL_URL') or str(_resolve_setting('STASH_URL', _default_url))).rstrip('/')
STASH_API_KEY = str(_resolve_setting('STASH_API_KEY', os.getenv('STASH_API_KEY', 'REPLACE_WITH_API_KEY')))
STASH_PUBLIC_BASE = str(_resolve_setting('STASH_PUBLIC_BASE', os.getenv('STASH_PUBLIC_BASE') or os.getenv('STASH_PUBLIC_URL') or ''))

_SCENE_FRAGMENT = (
    # Minimal but includes studio for UI badge and preview/screenshot URLs
    'id title rating100 '
    'paths { screenshot preview } '
    'studio { id name } '
    'performers { id name image_path } '
    'tags { id name } '
    'files { width height duration size path fingerprints { type value } }'
)

def _stub_scene(i: int) -> Dict[str, Any]:
    # Only used as a last‑resort when stash connection or tag query fails so recommenders remain functional.
    return {
        'id': i,
        'title': f'Scene {i}',
        'rating100': None,
        'studio': {'id': '0', 'name': 'Stub Studio'},
        'paths': {'screenshot': None, 'preview': None},
        'performers': [],
        'tags': [],
        'files': [{'width': None,'height': None,'duration': None,'size': None,'path': None,'fingerprints': []}],
    }

def _have_valid_api_key() -> bool:
    return bool(STASH_API_KEY and STASH_API_KEY != 'REPLACE_WITH_API_KEY')

_stash_client: Any | None = None
_stash_version: Any | None = None

def _rewrite_scene_paths(scene: Dict[str, Any]):
    """Rewrite screenshot/preview URLs to remove internal docker host and optionally apply PUBLIC base.

    Strategy:
      1. If STASH_PUBLIC_BASE provided, replace scheme+netloc with that base.
      2. Else strip scheme+netloc leaving a relative path.
    """
    try:
        p = scene.get('paths') if isinstance(scene, dict) else None
        if not isinstance(p, dict):
            return
        for key in ('screenshot', 'preview'):
            url = p.get(key)
            if not isinstance(url, str):
                continue
            if 'host.docker.internal' in url or STASH_PUBLIC_BASE:
                parsed = urlparse(url)
                new_path = parsed.path or ''
                if parsed.query:
                    new_path += '?' + parsed.query
                if STASH_PUBLIC_BASE:
                    p[key] = STASH_PUBLIC_BASE.rstrip('/') + new_path
                else:
                    p[key] = new_path
        # Also fix performer image_path fields
        performers = scene.get('performers')
        if isinstance(performers, list):
            for perf in performers:
                if isinstance(perf, dict):
                    img = perf.get('image_path')
                    if isinstance(img, str) and ( 'host.docker.internal' in img or STASH_PUBLIC_BASE ):
                        parsed = urlparse(img)
                        new_path = parsed.path or ''
                        if parsed.query:
                            new_path += '?' + parsed.query
                        perf['image_path'] = STASH_PUBLIC_BASE.rstrip('/') + new_path if STASH_PUBLIC_BASE else new_path
    except Exception:
        pass

def _build_connection_dict() -> Dict[str, Any]:
    parsed = urlparse(STASH_URL)
    scheme = parsed.scheme or 'http'
    # Extract hostname and port separately so stashapi doesn't append default port again
    hostname = parsed.hostname or 'localhost'
    # Determine explicit override precedence: env STASH_PORT > system setting STASH_PORT.
    override_port_raw = os.getenv('STASH_PORT')
    if override_port_raw is None:
        sp = sys_get('STASH_PORT', None)
        # Filter empty / None / 0 sentinel
        if sp not in (None, '', 0):
            override_port_raw = str(sp)
    port: int
    if override_port_raw:
        try:
            port = int(override_port_raw)
        except ValueError:
            print(f"[stash] invalid STASH_PORT override '{override_port_raw}', ignoring", flush=True)
            override_port_raw = None
    if not override_port_raw:
        # Use port from URL if present else default Stash 3000
        port = parsed.port if parsed.port else 3000
    conn = {
        'ApiKey': STASH_API_KEY,
        'Scheme': scheme,
        'Host': hostname,
        'Port': port,
    }
    print(f"[stash] build connection host={hostname} port={port} scheme={scheme} raw_url={STASH_URL}", flush=True)
    return conn

def get_stash() -> Any | None:
    global _stash_client, _stash_version
    if _stash_client is not None:
        return _stash_client
    if not StashInterface:
        if _IMPORT_ERR:
            print(f"[stash] StashInterface unavailable: {_IMPORT_ERR}", flush=True)
        return None
    if not _have_valid_api_key():
        print('[stash] API key missing / placeholder; cannot init StashInterface', flush=True)
        return None
    try:
        conn = _build_connection_dict()
        _stash_client = StashInterface(conn)  # type: ignore
        try:
            _stash_version = _stash_client.stash_version()
            print(f"[stash] connected to stash version {_stash_version}", flush=True)
        except Exception as e:  # pragma: no cover
            print(f"[stash] version probe failed: {e}", flush=True)
        return _stash_client
    except Exception as e:
        print(f"[stash] failed to create StashInterface: {e}", flush=True)
        traceback.print_exc()
        return None

def fetch_scenes_by_tag(tag_id: int, limit: int) -> List[Dict[str, Any]]:
    """Synchronous tag-based scene query using stashapi find_scenes with tag filter.

    This uses the stashapi wrapper (which is synchronous) and returns up to `limit`
    scenes having the given tag id. Falls back to stub scenes if stash unavailable.
    """
    client = get_stash()
    if not client:
        return [_stub_scene(i) for i in range(limit)]
    try:
        # According to stash GraphQL schema, tags filter uses tag_ids or tags? We'll try tag_ids.
        res = client.find_scenes(
            f={'tags': {'value': [tag_id], 'modifier': 'INCLUDES'}},
            filter={'per_page': limit},
            fragment=_SCENE_FRAGMENT
        ) or []
        if len(res) > limit:
            res = res[:limit]
        for sc in res:
            _rewrite_scene_paths(sc)
        return res
    except Exception as e:
        print(f"[stash] tag query failure tag={tag_id}: {e}", flush=True)
        traceback.print_exc()
        return [_stub_scene(i) for i in range(limit)]

def fetch_scenes_by_tag_paginated(tag_id: int, offset: int, limit: int) -> tuple[List[Dict[str, Any]], int, bool]:
    """Offset-based pagination for tag scenes.

    Stash GraphQL offers page/per_page semantics; to emulate offset we compute
    the starting page and may need to fetch additional pages if offset not aligned.
    For simplicity we over-fetch up to two pages and then slice locally.
    Returns (scenes_slice, total_estimate, has_more) where total_estimate is best-effort.
    """
    if offset < 0:
        offset = 0
    if limit <= 0:
        return [], 0, False
    client = get_stash()
    if not client:
        # fabricate deterministic stub corpus length of 500 for consistent UX
        total_stub = 500
        end = min(offset + limit, total_stub)
        scenes = [_stub_scene(i) for i in range(offset, end)]
        return scenes, total_stub, end < total_stub
    try:
        per_page = max(limit, 1)
        start_page = (offset // per_page) + 1
        aggregated: List[Dict[str, Any]] = []
        last_page_full = False
        # Always fetch the start page
        for p in (start_page, start_page + 1):
            if p < start_page:
                continue
            res = client.find_scenes(
                f={'tags': {'value': [tag_id], 'modifier': 'INCLUDES'}},
                filter={'per_page': per_page, 'page': p},
                fragment=_SCENE_FRAGMENT
            ) or []
            for sc in res:
                _rewrite_scene_paths(sc)
            aggregated.extend(res)
            last_page_full = len(res) == per_page
            if len(res) < per_page:
                break  # no further pages
        # Probe one extra page only if last fetched page was full and we still need to know
        has_more = False
        if last_page_full:
            probe_page = start_page + 2
            res_probe = client.find_scenes(
                f={'tags': {'value': [tag_id], 'modifier': 'INCLUDES'}},
                filter={'per_page': 1, 'page': probe_page},  # minimal probe
                fragment='id'
            ) or []
            has_more = len(res_probe) > 0
        approx_total = offset + len(aggregated)
        if has_more:
            approx_total += limit  # optimistic extension
        slice_start = offset - ((start_page - 1) * per_page)
        if slice_start < 0:
            slice_start = 0
        slice_end = slice_start + limit
        page_slice = aggregated[slice_start:slice_end]
        return page_slice, approx_total, has_more
    except Exception as e:
        print(f"[stash] paginated tag query failure tag={tag_id}: {e}", flush=True)
        traceback.print_exc()
        total_stub = 200
        end = min(offset + limit, total_stub)
        scenes = [_stub_scene(i) for i in range(offset, end)]
        return scenes, total_stub, end < total_stub
