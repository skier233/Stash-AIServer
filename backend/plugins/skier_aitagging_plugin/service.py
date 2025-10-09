from __future__ import annotations

import logging
import os
from typing import Mapping

from stash_ai_server.services.base import RemoteServiceBase
from stash_ai_server.services.registry import services
from stash_ai_server.actions.registry import action
from stash_ai_server.actions.models import ContextRule, ContextInput

from . import logic

_log = logging.getLogger(__name__)


class SkierAITaggingService(RemoteServiceBase):
    name = "skier.ai_tagging"
    description = "AI tagging and analysis service"
    max_concurrency = 10
    ready_endpoint = "/ready"
    readiness_cache_seconds = 30.0
    failure_backoff_seconds = 60.0

    def __init__(self) -> None:
        super().__init__()
        self._api_key: str | None = None
        self.reload_settings()

    def reload_settings(self) -> None:
        """Load settings from DB and environment variables."""
        cfg = self._load_settings()
        
        # Load server URL
        server_setting = cfg.get("server_url")
        if server_setting is not None:
            self.server_url = server_setting or None

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
        return await logic.tag_images(self, "detail", ctx, params)

    @action(
        id="skier.ai_tag.image.selected",
        label="Tag Selected Images",
        description="Generate tag suggestions for selected images",
        service="ai",
        result_kind="dialog",
        contexts=[ContextRule(pages=["images"], selection="multi")],
    )
    async def tag_image_selected(self, ctx: ContextInput, params: dict):
        return await logic.tag_images(self, "selected", ctx, params)

    @action(
        id="skier.ai_tag.image.page",
        label="Tag Page Images",
        description="Generate tag suggestions for all images on the current page",
        service="ai",
        result_kind="dialog",
        contexts=[ContextRule(pages=["images"], selection="page")],
    )
    async def tag_image_page(self, ctx: ContextInput, params: dict):
        return await logic.tag_images(self, "page", ctx, params)

    @action(
        id="skier.ai_tag.image.all",
        label="Tag All Images",
        description="Analyze every image in the library",
        service="ai",
        result_kind="dialog",
        contexts=[ContextRule(pages=["images"], selection="none")],
    )
    async def tag_image_all(self, ctx: ContextInput, params: dict):
        return await logic.tag_images(self, "all", ctx, params)

    # ------------------------------------------------------------------
    # Scene actions - use controller pattern to spawn child tasks
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
        return await logic.tag_scene_single(self, ctx, params)

    @action(
        id="skier.ai_tag.scene.selected",
        label="Tag Selected Scenes",
        description="Analyze selected scenes for tag segments",
        service="ai",
        result_kind="dialog",
        contexts=[ContextRule(pages=["scenes"], selection="multi")],
        controller=True,
    )
    async def tag_scene_selected(self, ctx: ContextInput, params: dict, task_record):
        return await logic.spawn_scene_batch(self, ctx, params, task_record)

    @action(
        id="skier.ai_tag.scene.page",
        label="Tag Page Scenes",
        description="Analyze every scene visible in the current list view",
        service="ai",
        result_kind="dialog",
        contexts=[ContextRule(pages=["scenes"], selection="page")],
        controller=True,
    )
    async def tag_scene_page(self, ctx: ContextInput, params: dict, task_record):
        return await logic.spawn_scene_batch(self, ctx, params, task_record)

    @action(
        id="skier.ai_tag.scene.all",
        label="Tag All Scenes",
        description="Analyze every scene in the library",
        service="ai",
        result_kind="dialog",
        contexts=[ContextRule(pages=["scenes"], selection="none")],
    )
    async def tag_scene_all(self, ctx: ContextInput, params: dict):
        # For "all" scope, just acknowledge the request
        return await logic.tag_scene_all(self, ctx, params)

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
        return await logic.spawn_scene_batch(self, ctx, params, task_record)


def register():
    services.register(SkierAITaggingService())
