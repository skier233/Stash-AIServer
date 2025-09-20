from __future__ import annotations
from typing import Any
from app.services.registry import ServiceBase, services
from app.actions.registry import action
from app.actions.models import ContextRule, ContextInput


class AIService(ServiceBase):
    name = 'ai'
    description = 'AI tagging and analysis service'
    server_url = 'http://ai-service:9000'  # placeholder external host

    @action(
        id='ai.tag.images',
        label='AI Tag Images',
        description='Generate tag suggestions for selected images',
        service='ai',
        result_kind='dialog',
        contexts=[ContextRule(pages=['images'], selection='both', min_selected=0)],
    )
    async def tag_images(self, ctx: ContextInput, params: dict) -> Any:
        # Stub simulated logic returning mock tags
        selected = ctx.selected_ids or ([ctx.entity_id] if ctx.entity_id else [])
        return {
            'targets': selected,
            'tags': [
                {'name': 'outdoor', 'confidence': 0.92},
                {'name': 'portrait', 'confidence': 0.81}
            ]
        }

    @action(
        id='ai.tag.scenes',
        label='AI Tag Scenes',
        description='Analyze scenes for tag segments',
        service='ai',
        result_kind='none',
        contexts=[ContextRule(pages=['scenes'], selection='both', min_selected=0)],
    )
    async def tag_scenes(self, ctx: ContextInput, params: dict) -> Any:
        # Stub: would enqueue long-running segmentation job later
        return {'message': 'scene tagging queued (stub)'}


def register():
    services.register(AIService())
