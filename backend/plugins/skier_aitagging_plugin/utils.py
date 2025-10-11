
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

def get_selected_items(ctx: ContextInput) -> list[str]:
    """Collect target IDs based on scope."""

    if ctx.is_detail_view:
        return [ctx.entity_id]
    elif ctx.selected_ids:
        return [ctx.selected_ids]
    elif ctx.visible_ids:
        return [ctx.visible_ids]
    else:
        # TODO
        return []