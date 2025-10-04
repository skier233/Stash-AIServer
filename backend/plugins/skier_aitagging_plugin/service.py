from stash_ai_server.services.registry import ServiceBase, services
from stash_ai_server.actions.registry import action, registry as action_registry
from stash_ai_server.actions.models import ContextRule, ContextInput

from . import logic


class SkierAITaggingService(ServiceBase):
    name = "skier.ai_tagging"
    description = "AI tagging and analysis service"
    server_url = None
    max_concurrency = 10

    # ------------------------------------------------------------------
    # Image actions
    # ------------------------------------------------------------------

    @action(
        id="skier.ai_tag.image",
        label="AI Tag Image",
        description="Generate tag suggestions for an image",
        service="ai",
        result_kind="dialog",
        contexts=[ContextRule(pages=["images"], selection="single")],
    )
    async def tag_image_single(self, ctx: ContextInput, params: dict):
        return await logic.tag_images("detail", ctx, params)

    @action(
        id="skier.ai_tag.image.selected",
        label="Tag Selected Images",
        description="Generate tag suggestions for selected images",
        service="ai",
        result_kind="dialog",
        contexts=[ContextRule(pages=["images"], selection="multi")],
    )
    async def tag_image_selected(self, ctx: ContextInput, params: dict):
        return await logic.tag_images("selected", ctx, params)

    @action(
        id="skier.ai_tag.image.page",
        label="Tag Page Images",
        description="Generate tag suggestions for all images on the current page",
        service="ai",
        result_kind="dialog",
        contexts=[ContextRule(pages=["images"], selection="page")],
    )
    async def tag_image_page(self, ctx: ContextInput, params: dict):
        return await logic.tag_images("page", ctx, params)

    @action(
        id="skier.ai_tag.image.all",
        label="Tag All Images",
        description="Analyze every image in the library",
        service="ai",
        result_kind="dialog",
        contexts=[ContextRule(pages=["images"], selection="none")],
    )
    async def tag_image_all(self, ctx: ContextInput, params: dict):
        return await logic.tag_images("all", ctx, params)

    # ------------------------------------------------------------------
    # Scene actions
    # ------------------------------------------------------------------

    @action(
        id="skier.ai_tag.scene",
        label="AI Tag Scene",
        description="Analyze a scene for tag segments",
        service="ai",
        result_kind="dialog",
        contexts=[ContextRule(pages=["scenes"], selection="single")],
    )
    async def tag_scene_single(self, ctx: ContextInput, params: dict):
        return await logic.tag_scenes("detail", ctx, params)

    @action(
        id="skier.ai_tag.scene.selected",
        label="Tag Selected Scenes",
        description="Analyze selected scenes for tag segments",
        service="ai",
        result_kind="dialog",
        contexts=[ContextRule(pages=["scenes"], selection="multi")],
    )
    async def tag_scene_selected(self, ctx: ContextInput, params: dict):
        return await logic.tag_scenes("selected", ctx, params)

    @action(
        id="skier.ai_tag.scene.page",
        label="Tag Page Scenes",
        description="Analyze every scene visible in the current list view",
        service="ai",
        result_kind="dialog",
        contexts=[ContextRule(pages=["scenes"], selection="page")],
    )
    async def tag_scene_page(self, ctx: ContextInput, params: dict):
        return await logic.tag_scenes("page", ctx, params)

    @action(
        id="skier.ai_tag.scene.all",
        label="Tag All Scenes",
        description="Analyze every scene in the library",
        service="ai",
        result_kind="dialog",
        contexts=[ContextRule(pages=["scenes"], selection="none")],
    )
    async def tag_scene_all(self, ctx: ContextInput, params: dict):
        return await logic.tag_scenes("all", ctx, params)

    # ------------------------------------------------------------------
    # Batch orchestration
    # ------------------------------------------------------------------

    @action(
        id="skier.ai_tag.batch.spawn.scenes",
        label="AI Batch Spawn Scenes",
        description="Spawn individual tagging subtasks for each selected scene",
        service="ai",
        result_kind="dialog",
        contexts=[ContextRule(pages=["scenes"], selection="multi")],
        controller=True,
    )
    async def batch_spawn_scenes(self, ctx: ContextInput, params: dict, task_record):
        return await logic.spawn_scene_batch(ctx, params, task_record)


def register():
    services.register(SkierAITaggingService())
