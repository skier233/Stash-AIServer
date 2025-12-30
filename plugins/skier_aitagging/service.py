from __future__ import annotations
from stash_ai_server.services.base import RemoteServiceBase
from stash_ai_server.services.registry import services
from stash_ai_server.actions.registry import action
from stash_ai_server.actions.models import ContextRule, ContextInput
from stash_ai_server.tasks.models import TaskRecord
from stash_ai_server.utils.stash_api import stash_api
from . import logic


def _coerce_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


class SkierAITaggingService(RemoteServiceBase):
    name = "AI_Tagging"
    description = "AI tagging and analysis service"
    max_concurrency = 10
    ready_endpoint = "/ready"
    readiness_cache_seconds = 30.0
    failure_backoff_seconds = 60.0

    def __init__(self) -> None:
        super().__init__()
        self._api_key: str | None = None
        self.apply_ai_tagged_tag: bool = True
        self.reload_settings()

    def reload_settings(self) -> None:
        """Load settings from DB and environment variables."""
        cfg = self._load_settings()
        
        # Load server URL
        server_setting = cfg.get("server_url")
        if server_setting is not None:
            self.server_url = server_setting or None

        self.apply_ai_tagged_tag = _coerce_bool(cfg.get("apply_ai_tagged_tag"), True)

    # ------------------------------------------------------------------
    # Image actions
    # ------------------------------------------------------------------

    @action(
        id="skier.ai_tag.image",
        label="AI Tag Image",
        description="Generate tag suggestions for an image",
        result_kind="dialog",
        contexts=[ContextRule(pages=["images"], selection="single")],
    )
    async def tag_image_single(self, ctx: ContextInput, params: dict, task_record: TaskRecord):
        return await logic.tag_images(self, ctx, params, task_record)

    @action(
        id="skier.ai_tag.image.selected",
        label="Tag Selected Images",
        description="Generate tag suggestions for selected images",
        result_kind="dialog",
        contexts=[ContextRule(pages=["images"], selection="multi")],
    )
    async def tag_image_selected(self, ctx: ContextInput, params: dict, task_record: TaskRecord):
        return await logic.tag_images(self, ctx, params, task_record)

    @action(
        id="skier.ai_tag.image.page",
        label="Tag Page Images",
        description="Generate tag suggestions for all images on the current page",
        result_kind="dialog",
        contexts=[ContextRule(pages=["images"], selection="page")],
    )
    async def tag_image_page(self, ctx: ContextInput, params: dict, task_record: TaskRecord):
        return await logic.tag_images(self, ctx, params, task_record)

    @action(
        id="skier.ai_tag.image.all",
        label="Tag All Images",
        description="Analyze every image in the library",
        result_kind="dialog",
        contexts=[ContextRule(pages=["images"], selection="none")],
    )
    async def tag_image_all(self, ctx: ContextInput, params: dict, task_record: TaskRecord):
        ctx.selected_ids = await stash_api.get_all_images_async()
        return await logic.tag_images(self, ctx, params, task_record)

    # ------------------------------------------------------------------
    # Scene actions - use controller pattern to spawn child tasks
    # ------------------------------------------------------------------

    @action(
        id="skier.ai_tag.scene",
        label="AI Tag Scene",
        description="Analyze a scene for tag segments",
        result_kind="dialog",
        contexts=[ContextRule(pages=["scenes"], selection="single")],
    )
    async def tag_scene_single(self, ctx: ContextInput, params: dict, task_record: TaskRecord):
        return await logic.tag_scenes(self, ctx, params, task_record)

    @action(
        id="skier.ai_tag.scene.selected",
        label="Tag Selected Scenes",
        description="Analyze selected scenes for tag segments",
        result_kind="dialog",
        contexts=[ContextRule(pages=["scenes"], selection="multi")],
    )
    async def tag_scene_selected(self, ctx: ContextInput, params: dict, task_record: TaskRecord):
        return await logic.tag_scenes(self, ctx, params, task_record)

    @action(
        id="skier.ai_tag.scene.page",
        label="Tag Page Scenes",
        description="Analyze every scene visible in the current list view",
        result_kind="dialog",
        contexts=[ContextRule(pages=["scenes"], selection="page")],
    )
    async def tag_scene_page(self, ctx: ContextInput, params: dict, task_record: TaskRecord):
        return await logic.tag_scenes(self, ctx, params, task_record)

    @action(
        id="skier.ai_tag.scene.all",
        label="Tag All Scenes",
        description="Analyze every scene in the library",
        result_kind="dialog",
        contexts=[ContextRule(pages=["scenes"], selection="none")],
    )
    async def tag_scene_all(self, ctx: ContextInput, params: dict, task_record: TaskRecord):
        ctx.selected_ids = await stash_api.get_all_scenes_async()
        return await logic.tag_scenes(self, ctx, params, task_record)

def register():
    services.register(SkierAITaggingService())