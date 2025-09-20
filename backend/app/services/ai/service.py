from __future__ import annotations
from typing import Any
from app.services.registry import ServiceBase, services
from app.actions.registry import action
from app.actions.models import ContextRule, ContextInput


class AIService(ServiceBase):
    name = 'ai'
    description = 'AI tagging and analysis service'
    server_url = 'http://ai-service:9000'  # placeholder external host
    # ------------------------------------------------------------------
    # Image Tagging (single + bulk variants under one logical id)
    # ------------------------------------------------------------------
    @action(
        id='ai.tag.image',
        label='AI Tag Image',
        description='Generate tag suggestions for an image',
        service='ai',
        result_kind='dialog',
        contexts=[ContextRule(pages=['images'], selection='single')],  # detail view
    )
    async def tag_image_single(self, ctx: ContextInput, params: dict) -> Any:
        selected = ctx.selected_ids or ([ctx.entity_id] if ctx.entity_id else [])
        return {
            'targets': selected,
            'tags': [
                {'name': 'outdoor', 'confidence': 0.92},
                {'name': 'portrait', 'confidence': 0.81}
            ]
        }

    @action(
        id='ai.tag.image',
        label='AI Tag Images',  # bulk label
        description='Generate tag suggestions for images',
        service='ai',
        result_kind='dialog',
        contexts=[ContextRule(pages=['images'], selection='multi')],  # library view
    )
    async def tag_image_bulk(self, ctx: ContextInput, params: dict) -> Any:
        selected = ctx.selected_ids or ([ctx.entity_id] if ctx.entity_id else [])
        return {
            'targets': selected,
            'tags': [
                {'name': 'outdoor', 'confidence': 0.92},
                {'name': 'portrait', 'confidence': 0.81}
            ]
        }

    # ------------------------------------------------------------------
    # Scene Tagging (single + bulk variants under one logical id)
    # ------------------------------------------------------------------
    @action(
        id='ai.tag.scenes',
        label='AI Tag Scene',
        description='Analyze a scene for tag segments',
        service='ai',
        result_kind='dialog',
        contexts=[ContextRule(pages=['scenes'], selection='single')],  # detail view
    )
    async def tag_scene_single(self, ctx: ContextInput, params: dict) -> Any:
        selected = ctx.selected_ids or ([ctx.entity_id] if ctx.entity_id else [])
        if not selected and ctx.entity_id:
            selected = [ctx.entity_id]
        if not selected:
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
            'summary': f'{len(selected)} scene(s) processed (stub single)'
        }

    @action(
        id='ai.tag.scenes',
        label='AI Tag Scenes',
        description='Analyze scenes for tag segments',
        service='ai',
        result_kind='dialog',
        contexts=[ContextRule(pages=['scenes'], selection='multi')],  # library view
    )
    async def tag_scene_bulk(self, ctx: ContextInput, params: dict) -> Any:
        selected = ctx.selected_ids or ([ctx.entity_id] if ctx.entity_id else [])
        if not selected:
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
