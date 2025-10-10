import asyncio
import logging
from .api_models import ImageResult
from backend.stash_ai_server.services.base import RemoteServiceBase

IMAGES_ENDPOINT = "/process_images/"  # Batch endpoint - accepts multiple image paths
SCENE_ENDPOINT = "/scene"    # Single scene endpoint - processes one scene at a time

_log = logging.getLogger(__name__)

async def call_images_api(service: RemoteServiceBase, image_paths: list[str], params: dict) -> ImageResult | None:
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