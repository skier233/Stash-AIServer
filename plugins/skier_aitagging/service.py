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

    # ------------------------------------------------------------------
    # Tag configuration methods (for plugin endpoints)
    # ------------------------------------------------------------------

    async def get_available_tags_data(self, include_disabled: bool = False) -> dict:
        """Get available tags grouped by model.
        
        Args:
            include_disabled: If True, include all tags regardless of enabled status.
                           If False (default), only return enabled tags.
        
        Returns:
            dict with 'tags' and 'models' keys, similar to AvailableTagsResponse
        """
        import httpx
        import logging
        from . import tag_config
        
        _log = logging.getLogger(__name__)
        
        if not self.server_url:
            return {'tags': [], 'models': [], 'error': 'AI server URL not configured'}
        
        # Get tag config
        tag_config_obj = tag_config.get_tag_configuration()
        
        # Fetch tags from AI server
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                url = f"{self.server_url.rstrip('/')}/tags/available"
                response = await client.get(url)
                response.raise_for_status()
                server_data = response.json()
        except Exception as exc:
            _log.warning("Failed to fetch tags from AI server: %s", exc)
            return {'tags': [], 'models': [], 'error': f'Failed to fetch from AI server: {str(exc)}'}
        
        server_tags = server_data.get('tags', [])
        server_models_data = server_data.get('models', [])
        
        # Build response
        tags_list = []
        models_list = []
        seen_tags = set()
        
        for model_data in server_models_data:
            model_name = model_data.get('name', '')
            model_display_name = model_data.get('displayName', model_data.get('display', model_name))
            model_tags = model_data.get('tags', [])
            category = model_data.get('categories', [])
            category_display = model_data.get('categoryDisplay', '')
            is_active = model_data.get('active', True)
            
            # Filter tags by enabled status if include_disabled is False
            filtered_model_tags = []
            for tag in model_tags:
                if isinstance(tag, dict):
                    tag_name = tag.get('tag', tag.get('name', ''))
                else:
                    tag_name = str(tag)
                
                if include_disabled:
                    # Include all tags
                    filtered_model_tags.append(tag)
                else:
                    # Only include enabled tags
                    is_enabled = tag_config_obj.get_tag_enabled_status(tag_name)
                    if is_enabled:
                        filtered_model_tags.append(tag)
            
            models_list.append({
                'name': model_name,
                'displayName': model_display_name,
                'category': category,
                'categoryDisplay': category_display,
                'tagCount': len(filtered_model_tags),
                'active': is_active,
                'tags': filtered_model_tags
            })
            
            for tag in filtered_model_tags:
                if isinstance(tag, dict):
                    tag_name = tag.get('tag', tag.get('name', ''))
                else:
                    tag_name = str(tag)
                tag_key = f"{tag_name}::{model_name}"
                if tag_key not in seen_tags:
                    seen_tags.add(tag_key)
                    tags_list.append({
                        'tag': tag_name,
                        'model': model_name,
                        'modelDisplayName': model_display_name,
                        'category': category,
                        'categoryDisplay': category_display
                    })
        
        return {'tags': tags_list, 'models': models_list}

    def get_enabled_tags_list(self) -> list[str]:
        """Get list of enabled tag names (normalized, lowercase)."""
        from . import tag_config
        tag_config_obj = tag_config.get_tag_configuration()
        return tag_config_obj.get_enabled_tags()

    async def get_all_tag_statuses(self) -> dict[str, bool]:
        """Get all tag enabled statuses, including tags from AI server that aren't in CSV yet.
        
        Returns:
            Dictionary mapping tag names (normalized, lowercase) to enabled status.
            Tags not in CSV default to True (enabled).
        """
        import httpx
        import logging
        from . import tag_config
        
        _log = logging.getLogger(__name__)
        
        # Get statuses from CSV
        tag_config_obj = tag_config.get_tag_configuration()
        csv_statuses = tag_config_obj.get_all_tag_statuses()
        
        # If we have a server URL, also fetch all tags from AI server and merge
        if self.server_url:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    url = f"{self.server_url.rstrip('/')}/tags/available"
                    response = await client.get(url)
                    response.raise_for_status()
                    server_data = response.json()
                    
                    # Get all unique tag names from server
                    server_tags = server_data.get('tags', [])
                    server_models_data = server_data.get('models', [])
                    
                    all_server_tags = set()
                    for model_data in server_models_data:
                        model_tags = model_data.get('tags', [])
                        for tag in model_tags:
                            if isinstance(tag, dict):
                                tag_name = tag.get('tag', tag.get('name', ''))
                            else:
                                tag_name = str(tag)
                            if tag_name:
                                all_server_tags.add(tag_name.lower())
                    
                    # Merge: CSV statuses take precedence, but include all server tags (default True)
                    merged_statuses = {}
                    for tag_name in all_server_tags:
                        if tag_name in csv_statuses:
                            merged_statuses[tag_name] = csv_statuses[tag_name]
                        else:
                            # Tag not in CSV, default to enabled (True)
                            merged_statuses[tag_name] = True
                    
                    return merged_statuses
            except Exception as exc:
                _log.warning("Failed to fetch tags from AI server for status merge: %s", exc)
                # Fall back to CSV-only statuses
                return csv_statuses
        
        # No server URL, just return CSV statuses
        return csv_statuses

    def update_tag_enabled_status(self, tag_statuses: dict[str, bool] | None = None, enabled_tags: list[str] | None = None, disabled_tags: list[str] | None = None) -> dict:
        """Update enabled status for tags.
        
        Args:
            tag_statuses: Dictionary mapping tag names (normalized, lowercase) to enabled status (preferred)
            enabled_tags: List of tag names to enable (alternative to tag_statuses)
            disabled_tags: List of tag names to disable (alternative to tag_statuses)
        
        Returns:
            dict with 'status' and 'updated' count
        """
        from . import tag_config
        tag_config_obj = tag_config.get_tag_configuration()
        
        # If tag_statuses provided, use it directly
        if tag_statuses is not None:
            tag_config_obj.update_tag_enabled_status(tag_statuses)
            return {'status': 'ok', 'updated': len(tag_statuses)}
        
        # Otherwise, get current statuses and update based on enabled/disabled lists
        current_statuses = tag_config_obj.get_all_tag_statuses()
        updated_map = dict(current_statuses)
        
        if enabled_tags:
            for tag in enabled_tags:
                updated_map[tag.lower()] = True
        
        if disabled_tags:
            for tag in disabled_tags:
                updated_map[tag.lower()] = False
        
        tag_config_obj.update_tag_enabled_status(updated_map)
        return {'status': 'ok', 'updated': len(updated_map)}

def register():
    services.register(SkierAITaggingService())