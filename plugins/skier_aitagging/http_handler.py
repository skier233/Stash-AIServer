import asyncio
import logging
from typing import Sequence
from .models import AIModelInfo, AIVideoResultV3, ImageResult, VideoServerResponse
from stash_ai_server.services.base import RemoteServiceBase
from stash_ai_server.core.system_settings import get_value as sys_get_value

IMAGES_ENDPOINT = "/v3/process_images/"  # Batch endpoint - accepts multiple image paths
SCENE_ENDPOINT = "/v3/process_video/"    # Single scene endpoint - processes one scene at a time
ACTIVE_SCENE_MODELS = "/v3/current_ai_models/"


_log = logging.getLogger(__name__)

async def call_images_api(service: RemoteServiceBase, image_paths: list[str]) -> ImageResult | None:
    """Call the /images endpoint with a batch of image paths."""
    try:
        # Get excluded tags from system settings
        excluded_tags = sys_get_value('EXCLUDED_TAGS', [])
        if excluded_tags is None:
            excluded_tags = []
        if isinstance(excluded_tags, str):
            import json
            try:
                excluded_tags = json.loads(excluded_tags)
            except:
                excluded_tags = []
        if not isinstance(excluded_tags, list):
            excluded_tags = []
        
        _log.info(
            "call_images_api: Retrieved excluded_tags from system settings: %s (count=%d)",
            excluded_tags,
            len(excluded_tags) if isinstance(excluded_tags, list) else 0
        )
        
        payload = {
            "paths": image_paths,
            "threshold": 0.5,
            "return_confidence": False
        }
        if excluded_tags:
            payload["excluded_tags"] = excluded_tags
            _log.info(
                "call_images_api: Added excluded_tags to payload: %s",
                excluded_tags
            )
        else:
            _log.info("call_images_api: No excluded_tags to add to payload")
        
        return await service.http.post(
            IMAGES_ENDPOINT,
            json=payload,
            response_model=ImageResult,
        )
    except asyncio.CancelledError:  # pragma: no cover
        raise
    except Exception as exc:  # noqa: BLE001
        _log.warning("images API call failed: %s", exc)
        raise

async def call_scene_api(
    service: RemoteServiceBase,
    scene_path: str,
    frame_interval: float,
    vr_video: bool,
    *,
    threshold: float,
    skip_categories: Sequence[str] | None = None,
) -> VideoServerResponse | None:
    """Call the /scene endpoint for a single scene."""   
    try:
        # Get excluded tags from system settings
        excluded_tags = sys_get_value('EXCLUDED_TAGS', [])
        if excluded_tags is None:
            excluded_tags = []
        if isinstance(excluded_tags, str):
            import json
            try:
                excluded_tags = json.loads(excluded_tags)
            except:
                excluded_tags = []
        if not isinstance(excluded_tags, list):
            excluded_tags = []
        
        _log.info(
            "call_scene_api: Retrieved excluded_tags from system settings: %s (count=%d)",
            excluded_tags,
            len(excluded_tags) if isinstance(excluded_tags, list) else 0
        )
        
        payload = {
            "path": scene_path,
            "frame_interval": frame_interval,
            "return_confidence": True,
            "vr_video": vr_video,
            "threshold": threshold,
        }
        if skip_categories:
            payload["categories_to_skip"] = list(skip_categories)
        if excluded_tags:
            payload["excluded_tags"] = excluded_tags
            _log.info(
                "call_scene_api: Added excluded_tags to payload: %s",
                excluded_tags
            )
        else:
            _log.info("call_scene_api: No excluded_tags to add to payload")
        
        _log.debug(
            "call_scene_api: Sending request to %s with payload keys: %s",
            SCENE_ENDPOINT,
            list(payload.keys())
        )
        
        return await service.http.post(
            SCENE_ENDPOINT,
            json=payload,
            response_model=VideoServerResponse
        )
    except asyncio.CancelledError:  # pragma: no cover
        raise
    except Exception as exc:  # noqa: BLE001
        _log.warning("scene API call failed for scene_path=%s: %s", scene_path, exc)
        return None
    
async def get_active_scene_models(service: RemoteServiceBase) -> list[AIModelInfo]:
    """Fetch the list of active models from the remote service."""
    try:
        return await service.http.get(
            ACTIVE_SCENE_MODELS,
            response_model=list[AIModelInfo],
        )
        
    except asyncio.CancelledError:  # pragma: no cover
        raise
    except Exception as exc:  # noqa: BLE001
        _log.warning("Failed to fetch active models: %s", exc)
        return []