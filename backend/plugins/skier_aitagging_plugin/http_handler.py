import asyncio
import logging
from .models import AIModelInfo, AIVideoResultV3, ImageResult
from stash_ai_server.services.base import RemoteServiceBase

IMAGES_ENDPOINT = "/process_images/"  # Batch endpoint - accepts multiple image paths
SCENE_ENDPOINT = "/v3/process_video/"    # Single scene endpoint - processes one scene at a time
ACTIVE_SCENE_MODELS = "/v3/current_ai_models/"


_log = logging.getLogger(__name__)

async def call_images_api(service: RemoteServiceBase, image_paths: list[str]) -> ImageResult | None:
    """Call the /images endpoint with a batch of image paths."""
    try:
        payload = {
            "paths": image_paths,
            "threshold": 0.5,
            "return_confidence": False
        }
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

async def call_scene_api(service: RemoteServiceBase, scene_path: str, frame_interval: float, vr_video: bool) -> AIVideoResultV3 | None:
    """Call the /scene endpoint for a single scene."""   
    try:
        payload = {
            "path": scene_path,
            "frame_interval": frame_interval,
            "return_confidence": True,
            "vr_video": vr_video,
            "threshold": 0.5
        }
        return await service.http.post(
            SCENE_ENDPOINT,
            json=payload,
            response_model=AIVideoResultV3
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