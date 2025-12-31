import asyncio
import logging
from typing import Sequence
from .models import AIModelInfo, AIVideoResultV3, ImageResult, VideoServerResponse
from stash_ai_server.services.base import RemoteServiceBase

IMAGES_ENDPOINT = "/v3/process_images/"  # Batch endpoint - accepts multiple image paths
SCENE_ENDPOINT = "/v3/process_video/"    # Single scene endpoint - processes one scene at a time
ACTIVE_SCENE_MODELS = "/v3/current_ai_models/"


_log = logging.getLogger(__name__)

async def call_images_api(service: RemoteServiceBase, image_paths: list[str]) -> ImageResult | None:
    """Call the /images endpoint with a batch of image paths."""
    try:
        # Get disabled tags from plugin's tag_config (tags that are not enabled)
        excluded_tags = []
        try:
            from . import tag_config
            tag_config_obj = tag_config.get_tag_configuration()
            all_statuses = tag_config_obj.get_all_tag_statuses()
            # Build list of disabled tags (tags where enabled is False)
            excluded_tags = [tag_name for tag_name, enabled in all_statuses.items() if enabled is False]
        except Exception as exc:
            _log.warning("Failed to get disabled tags from tag_config: %s", exc)
        
        payload = {
            "paths": image_paths,
            "threshold": 0.5,
            "return_confidence": False
        }
        # Always include excluded_tags in payload, even if empty, for consistency
        payload["excluded_tags"] = excluded_tags
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
        # Get disabled tags from plugin's tag_config (tags that are not enabled)
        excluded_tags = []
        try:
            from . import tag_config
            tag_config_obj = tag_config.get_tag_configuration()
            all_statuses = tag_config_obj.get_all_tag_statuses()
            # Build list of disabled tags (tags where enabled is False)
            excluded_tags = [tag_name for tag_name, enabled in all_statuses.items() if enabled is False]
        except Exception as exc:
            _log.warning("Failed to get disabled tags from tag_config: %s", exc)
        
        payload = {
            "path": scene_path,
            "frame_interval": frame_interval,
            "return_confidence": True,
            "vr_video": vr_video,
            "threshold": threshold,
        }
        if skip_categories:
            payload["categories_to_skip"] = list(skip_categories)
        # Always include excluded_tags in payload, even if empty, for consistency
        payload["excluded_tags"] = excluded_tags
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