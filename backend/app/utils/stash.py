from __future__ import annotations
from typing import List, Dict, Any, Optional
import os, random, asyncio, time, traceback
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
STASH_URL = os.getenv('STASH_INTERNAL_URL') or os.getenv('STASH_URL', _default_url)
STASH_URL = STASH_URL.rstrip('/')
STASH_API_KEY = os.getenv('STASH_API_KEY', 'REPLACE_WITH_API_KEY')
STASH_PUBLIC_BASE = os.getenv('STASH_PUBLIC_BASE') or os.getenv('STASH_PUBLIC_URL') or ''  # optional public/base override for asset URLs

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

async def _fetch_with_stub(ids: List[int]) -> List[Dict[str, Any]]:
    return [_stub_scene(i) for i in ids]

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
        for key in ('screenshot','preview'):
            url = p.get(key)
            if not isinstance(url, str):
                continue
            if 'host.docker.internal' in url or STASH_PUBLIC_BASE:
                from urllib.parse import urlparse
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
                        from urllib.parse import urlparse
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
    # Env override first, then explicit URL port, else default 9999
    env_port = os.getenv('STASH_PORT')
    try:
        port = int(env_port) if env_port else (parsed.port if parsed.port else 9999)
    except ValueError:
        print(f"[stash] invalid STASH_PORT='{env_port}', falling back to parsed/default", flush=True)
        port = parsed.port if parsed.port else 9999
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

async def fetch_scenes_by_ids(ids: List[int]) -> List[Dict[str, Any]]:
    """Hydrate using stashapi find_scenes scene_ids argument; fallback to per-id if needed; else stub."""
    if not ids:
        return []
    client = get_stash()
    if not client:
        return await _fetch_with_stub(ids)

    # Deduplicate while preserving original order mapping for reordering
    seen = set()
    deduped: List[int] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            deduped.append(i)

    CHUNK = 80  # leverage server capability; adjust if needed
    collected: List[Dict[str, Any]] = []
    had_failure = False
    for start in range(0, len(deduped), CHUNK):
        batch = deduped[start:start+CHUNK]
        try:
            # Use scene_ids param directly (stashapi builds query with scene_ids var)
            res = client.find_scenes(scene_ids=batch, fragment=_SCENE_FRAGMENT) or []
            collected.extend(res)
        except Exception as e:
            print(f"[stash] batch hydrate error scene_ids {batch[:5]}... size={len(batch)}: {e}", flush=True)
            traceback.print_exc()
            had_failure = True

    if not collected and not had_failure:
        # Nothing returned but no explicit exception; fallback to stub
        return await _fetch_with_stub(ids)

    if had_failure:
        # Attempt slow per-id fallback for only missing ones
        have = {int(s.get('id')) for s in collected if isinstance(s, dict) and 'id' in s}
        missing = [i for i in deduped if i not in have]
        if missing:
            print(f"[stash] attempting per-id fallback for {len(missing)} missing scenes", flush=True)
            for mid in missing:
                try:
                    single = client.find_scene(mid, fragment=_SCENE_FRAGMENT)  # type: ignore
                    if single:
                        collected.append(single)
                except Exception as e:  # pragma: no cover
                    print(f"[stash] per-id fallback failed id={mid}: {e}", flush=True)

    by_id = {int(s.get('id')): s for s in collected if isinstance(s, dict) and 'id' in s}
    ordered = [by_id.get(i) for i in ids]
    realized = [s for s in ordered if s]
    if not realized:
        return await _fetch_with_stub(ids)
    for sc in realized:
        _rewrite_scene_paths(sc)
    return realized

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

async def hydrate_scene_ids(ids: List[int]) -> List[Dict[str, Any]]:
    scenes = await fetch_scenes_by_ids(ids)
    for sc in scenes:
        if not isinstance(sc, dict):
            continue
        for k in ('performers','tags','files','scene_markers','galleries','groups'):
            v = sc.get(k)
            if not isinstance(v, list):
                sc[k] = []
        paths = sc.get('paths')
        if not isinstance(paths, dict):
            sc['paths'] = {'screenshot': None, 'preview': None}
        else:
            paths.setdefault('screenshot', None)
            paths.setdefault('preview', None)
        if 'studio' not in sc or not sc['studio']:
            sc['studio'] = None
        # Normalize files -> ensure fingerprints array exists to prevent SceneCard .find errors
        for f in sc.get('files', []):  # type: ignore
            if isinstance(f, dict):
                if 'fingerprints' not in f or not isinstance(f['fingerprints'], list):
                    f['fingerprints'] = []
    return scenes

# Simple ID sampler (replace with real queries later)
BASE_IDS = [14632,14586,14466,14447]

def sample_scene_ids(n: int) -> List[int]:
    # Deterministic repeating sample for now
    out: List[int] = []
    while len(out) < n:
        out.extend(BASE_IDS)
    return out[:n]

async def fetch_recent_scene_ids(limit: int) -> List[int]:
    """Stub for 'recent' ordering – returns sampled IDs (deterministic order).

    Replace with real created_at DESC query via stashapp-tools later.
    """
    return sample_scene_ids(limit)
