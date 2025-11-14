import asyncio
import logging
from typing import Any, Dict, List
from urllib.parse import urlparse

from stash_ai_server.core.config import settings
from stash_ai_server.core.system_settings import get_value as sys_get
from stash_ai_server.core.runtime import register_backend_refresh_handler
from stash_ai_server.utils.url_helpers import dockerize_localhost
from stashapi.stashapp import StashInterface

_log = logging.getLogger(__name__)

class StashAPI:
    stash_url: str
    api_key: str | None
    stash_interface: StashInterface | None = None
    tag_id_cache: Dict[str, int] = {}
    tag_name_cache: Dict[int, str] = {}
    _effective_url: str | None = None

    def __init__(self) -> None:
        self.tag_id_cache = {}
        self.tag_name_cache = {}
        self.stash_interface = None
        self.stash_url = ''
        self.api_key = None
        self.refresh_configuration()

    def refresh_configuration(self) -> None:
        """Reload connection configuration from system settings."""

        new_url = sys_get("STASH_URL")
        new_key = sys_get("STASH_API_KEY")

        if not new_url:
            self.stash_url = ""
            self.api_key = new_key
            self.stash_interface = None
            self.tag_id_cache.clear()
            self.tag_name_cache.clear()
            _log.warning("STASH_URL not configured; Stash interface unavailable")
            return

        effective_url = dockerize_localhost(new_url, enabled=settings.docker_mode)

        try:
            new_interface = _construct_stash_interface(effective_url, new_key)
        except Exception as exc:
            _log.error(
                "Failed to configure Stash API client with url=%s: %s",
                new_url,
                exc,
            )
            return

        self.stash_url = new_url
        self._effective_url = effective_url
        self.api_key = new_key
        self.stash_interface = new_interface
        self.tag_id_cache.clear()
        self.tag_name_cache.clear()
        if effective_url != new_url:
            _log.info(
                "Stash API client configured host=%s (effective=%s)",
                self.stash_url,
                effective_url,
            )
        else:
            _log.info("Stash API client configured host=%s", self.stash_url)

    # Tags
    
    def fetch_tag_id(self, tag_name: str, parent_id: int | None = None, create_if_missing: bool = False, use_cache: bool = True, add_to_cache: Dict[str, int] = None) -> int | None:
        if use_cache and tag_name in self.tag_id_cache:
            return self.tag_id_cache[tag_name]
        
        if create_if_missing:
            if parent_id is None:
                tag = self.stash_interface.find_tag(tag_name, create=True)["id"]
            else:
                tag = self.stash_interface.find_tag(tag_name)
                if tag is None:
                    tag = self.stash_interface.create_tag({"name":tag_name, "ignore_auto_tag": True, "parent_ids":[parent_id]})
                tag = tag["id"] if tag else None
        else:
            tag = self.stash_interface.find_tag(tag_name)
            tag = tag["id"]  if tag else None
        if tag:
            self.tag_id_cache[tag_name] = tag
            self.tag_name_cache[tag] = tag_name 
            if add_to_cache is not None and tag_name not in add_to_cache:
                add_to_cache[tag_name] = tag
            return tag
        return None

    def get_tags_with_parent(self, parent_tag_id: int) -> Dict[str, int]:
        return {item['name']: item['id'] for item in self.stash_interface.find_tags(f={"parents": {"value":parent_tag_id, "modifier":"INCLUDES"}}, fragment="id name")}

    def get_stash_tag_name(self, tag_id: int) -> str | None:
        """Get the tag name for a given tag ID from Stash."""
        if tag_id in self.tag_name_cache:
            return self.tag_name_cache[tag_id]
        try:
            tag_data = self.stash_interface.find_tag(tag_id)
            if tag_data and "name" in tag_data:
                self.tag_name_cache[tag_id] = tag_data["name"]
                self.tag_id_cache[tag_data["name"]] = tag_id
                return tag_data["name"]
            return None
        except Exception:
            _log.exception("Failed to get tag name for tag_id=%s", tag_id)
            return None

    # Images    
    async def remove_tags_from_images_async(self, image_ids: list[int], tag_ids: list[int]) -> bool:
        await asyncio.to_thread(self.stash_interface.update_images, {"ids": image_ids, "tag_ids": {"ids": tag_ids, "mode": "REMOVE"}})

    def remove_tags_from_images(self, image_ids: list[int], tag_ids: list[int]) -> bool:
        self.stash_interface.update_images({"ids": image_ids, "tag_ids": {"ids": tag_ids, "mode": "REMOVE"}})

    async def add_tags_to_images_async(self, image_ids: list[int], tag_ids: list[int]) -> bool:
        await asyncio.to_thread(self.stash_interface.update_images, {"ids": image_ids, "tag_ids": {"ids": tag_ids, "mode": "ADD"}})

    def add_tags_to_images(self, image_ids: list[int], tag_ids: list[int]) -> bool:
        self.stash_interface.update_images({"ids": image_ids, "tag_ids": {"ids": tag_ids, "mode": "ADD"}})

    async def get_image_paths_async(self, images_ids: list[int]) -> Dict[int, str]:
        return await asyncio.to_thread(self.get_image_paths, images_ids)
    
    def get_image_paths(self, images_ids: list[int]) -> Dict[int, str]:
        """Fetch image paths for given image IDs."""
        out: Dict[int, str] = {}
        if not self.stash_interface:
            _log.warning("Stash interface not configured; returning empty image path map")
            return out
        try:
            images = self.stash_interface.find_images(image_ids=images_ids, fragment="id files {path}")
            _log.warning(f"Images: {images}")
        except Exception as exc:  # pragma: no cover - defensive
            _log.warning("Failed to fetch images for ids=%s: %s", images_ids, exc)
            return out
        for img in images or []:
            try:
                out[int(img["id"])] = img["files"][0]["path"]
            except Exception:
                # defensive: skip malformed entries
                continue
        _log.warning("Fetched image paths for ids=%s -> %s", images_ids, out)
        return out
    
    async def get_all_images_async(self) -> List[str]:
        """Fetch all image IDs from Stash."""
        return await asyncio.to_thread(self.get_all_images)

    def get_all_images(self) -> List[str]:
        """Fetch all image IDs from Stash."""
        image_ids: List[str] = []
        if not self.stash_interface:
            _log.warning("Stash interface not configured; returning empty image list")
            return image_ids
        try:
            images = self.stash_interface.find_images(
                fragment="id"
            )
            image_ids = [str(image["id"]) for image in images or [] if "id" in image]
        except Exception as exc:  # pragma: no cover - defensive
            _log.warning("Failed to fetch all images: %s", exc)
        _log.info("Fetched total %d images", len(image_ids))
        return image_ids

    # Scenes
    async def get_all_scenes_async(self) -> List[str]:
        """Fetch all scene IDs from Stash."""
        return await asyncio.to_thread(self.get_all_scenes)

    def get_all_scenes(self) -> List[str]:
        """Fetch all scene IDs from Stash."""
        scene_ids: List[str] = []
        if not self.stash_interface:
            _log.warning("Stash interface not configured; returning empty scene list")
            return scene_ids
        try:
            scenes = self.stash_interface.find_scenes(
                fragment="id"
            )
            scene_ids = [str(scene["id"]) for scene in scenes or [] if "id" in scene]
        except Exception as exc:  # pragma: no cover - defensive
            _log.warning("Failed to fetch all scenes: %s", exc)
        _log.info("Fetched total %d scenes", len(scene_ids))
        return scene_ids

    async def get_scene_path_and_tags_and_duration_async(self, scene_id: int):
        return await asyncio.to_thread(self.get_scene_path_and_tags_and_duration, scene_id)

    def get_scene_path_and_tags_and_duration(self, scene_id: int):
        scene_result = self.stash_interface.find_scene(id=scene_id, fragment="files {path duration} tags {id}")
        if not scene_result or 'files' not in scene_result or not scene_result['files']:
            return None, [], None
        path = scene_result['files'][0]['path']
        duration = scene_result['files'][0].get('duration')
        tags = [tag['id'] for tag in scene_result.get('tags', []) if 'id' in tag]
        return path, tags, duration

    def fetch_scenes_by_tag_paginated(self, tag_id: int, offset: int, limit: int) -> tuple[List[Dict[str, Any]], int, bool]:
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
        client = self.stash_interface
        _SCENE_FRAGMENT = (
            # Minimal but includes studio for UI badge and preview/screenshot URLs
            'id title rating100 '
            'paths { screenshot preview } '
            'studio { id name } '
            'performers { id name image_path } '
            'tags { id name } '
            'files { width height duration size path fingerprints { type value } }'
        )
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
            return [], 0, False

    async def add_tags_to_scene_async(self, scene_id: int, tag_ids: list[int]) -> None:
        await asyncio.to_thread(self.add_tags_to_scene, scene_id, tag_ids)

    def add_tags_to_scene(self, scene_id: int, tag_ids: list[int]) -> None:
        if not tag_ids:
            return
        payload = {
            "ids": [scene_id],
            "tag_ids": {
                "ids": tag_ids,
                "mode": "ADD",
            },
        }
        self.stash_interface.update_scenes(payload)

    async def remove_tags_from_scene_async(self, scene_id: int, tag_ids: list[int]) -> None:
        await asyncio.to_thread(self.remove_tags_from_scene, scene_id, tag_ids)

    def remove_tags_from_scene(self, scene_id: int, tag_ids: list[int]) -> None:
        if not tag_ids:
            return
        payload = {
            "ids": [scene_id],
            "tag_ids": {
                "ids": tag_ids,
                "mode": "REMOVE",
            },
        }
        self.stash_interface.update_scenes(payload)
    
    # Scene Markers

    async def destroy_scene_markers_async(self, marker_ids: list[int]):
        await asyncio.to_thread(self.destroy_scene_markers, marker_ids)

    def destroy_scene_markers(self, marker_ids: list[int]):
        self.stash_interface.destroy_markers(marker_ids)

    async def destroy_markers_with_tags_async(self, scene_id, tag_ids: list[int]):
        await asyncio.to_thread(self.destroy_markers_with_tags, scene_id, tag_ids)

    def destroy_markers_with_tags(self, scene_id, tag_ids: list[int]):
        markers = self.stash_interface.find_scene_markers(
            scene_marker_filter={
                "tags": {"value": tag_ids, "modifier": "INCLUDES"},
                "scenes": {"value": [scene_id], "modifier": "INCLUDES"}
            },
            fragment="id"
        )
        marker_ids = [marker['id'] for marker in markers] if markers else []
        if marker_ids:
            self.destroy_scene_markers(marker_ids)

    async def create_scene_markers_async(self, scene_id: int,timespans: Dict[tuple[int, str], list[tuple[float, float]]]):
        await asyncio.to_thread(self.create_scene_markers, scene_id, timespans)

    def create_scene_markers(self, scene_id: int,timespans: Dict[tuple[int, str], list[tuple[float, float]]]):
        for (tag_id, tag_name), spans in timespans.items():
            for start, end in spans:
                marker_data = {
                    "scene_id": scene_id,
                    "seconds": start,
                    "end_seconds": end,
                    "primary_tag_id": tag_id,
                    "tag_ids": [tag_id],
                    "title": tag_name,
                }
                self.stash_interface.create_scene_marker(marker_data)
        

def _have_valid_api_key(api_key) -> bool:
    return bool(api_key and api_key != 'REPLACE_WITH_API_KEY' and api_key.strip() != '')

def _construct_stash_interface(url: str, api_key: str = None) -> StashInterface:
    """Construct a StashInterface from environment variables."""
    parsed = urlparse(url)
    scheme = parsed.scheme or 'http'
    # Extract hostname and port separately so stashapi doesn't append default port again
    hostname = parsed.hostname or 'localhost'
    port = parsed.port if parsed.port else 3000
    conn: Dict[str, Any] = {
        'Scheme': scheme,
        'Host': hostname,
        'Port': port,
    }
    if _have_valid_api_key(api_key):
        conn['ApiKey'] = api_key
    return StashInterface(conn)

stash_api = StashAPI()


def _refresh_stash_api() -> None:
    try:
        stash_api.refresh_configuration()
    except Exception:  # pragma: no cover - defensive logging
        _log.exception("Stash API refresh failed")


register_backend_refresh_handler("stash_api", _refresh_stash_api)