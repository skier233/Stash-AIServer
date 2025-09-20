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
        # Previously 'none' so frontend ignored the returned payload. Use 'dialog' so user sees structured output.
        result_kind='dialog',
        contexts=[ContextRule(pages=['scenes'], selection='both', min_selected=0)],
    )
    async def tag_scenes(self, ctx: ContextInput, params: dict) -> Any:
        # Stub enhanced: pretend we analyzed scenes and found candidate tags per scene.
        selected = ctx.selected_ids or ([ctx.entity_id] if ctx.entity_id else [])
        if not selected:
            # Provide a default demonstration scene id placeholder
            selected = ['demo-scene-1']
        per_scene = []
        for sid in selected:
            per_scene.append({
                'scene_id': sid,
                'suggested_tags': [
                    {'name': 'hard-light', 'confidence': 0.74},
                    {'name': 'dialogue-heavy', 'confidence': 0.63}
                ],
                'notes': 'Mock inference data – replace with real model output.'
            })
        return {
            'targets': selected,
            'scenes': per_scene,
            'summary': f'{len(selected)} scene(s) processed (stub)' 
        }


def register():
    services.register(AIService())
