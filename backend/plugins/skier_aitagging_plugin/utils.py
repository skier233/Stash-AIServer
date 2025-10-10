
from stash_ai_server.actions.models import ContextInput

from .models import Scope


def extract_tags_from_response(response: dict) -> list[str]:
    """
    Extract tags from the API response.

    Args:
        response (dict): The response from the AI tagging service.

    Returns:
        list[str]: A list of tags that meet the confidence threshold.
    """
    tags = []
    for item in response.values():
        for tag_info in item:
            if isinstance(tag_info, (str, int)):
                tags.append(str(tag_info))
            elif isinstance(tag_info, str):
                tags.append(tag_info)
    return tags

def get_selected_items(scope: Scope, ctx: ContextInput) -> list[str]:
    """Collect target IDs based on scope."""
    selected = list(ctx.selected_ids or [])
    visible = list(ctx.visible_ids or [])
    entity = [ctx.entity_id] if ctx.entity_id else []

    if scope == "detail":
        return entity or selected
    if scope == "selected":
        return selected or entity
    if scope == "page":
        return visible
    if scope == "all":
        return []
    raise ValueError(f"Unknown scope '{scope}'")