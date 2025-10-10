import logging
from stash_ai_server.utils.stash_api_real import stash_api

_log = logging.getLogger(__name__)

AI_Base_Tag_Name = "AI"
AI_Base_Tag_Id = stash_api.fetch_tag_id(AI_Base_Tag_Name, create_if_missing=True)

AI_Error_Tag_Name = "AI_Errored"
AI_Error_Tag_Id = stash_api.fetch_tag_id(AI_Error_Tag_Name, parent_id=AI_Base_Tag_Id, create_if_missing=True)

AI_tags_cache = stash_api.get_tags_with_parent(parent_tag_id=AI_Base_Tag_Id)

AI_tags_cache[AI_Error_Tag_Name] = AI_Error_Tag_Id

def remove_ai_tags_from_images(image_ids: list[int]) -> None:
    """Remove all AI tags from the given images."""
    if not AI_tags_cache:
        _log.warning("No AI tags in cache; nothing to remove")
        return
    stash_api.remove_tags_from_images(image_ids, list(AI_tags_cache.values()))

def add_error_tag_to_images(image_ids: list[int]) -> None:
    """Add the AI_Errored tag to the given images."""
    if AI_Error_Tag_Id is None:
        _log.warning("AI_Error_Tag_Id is None; cannot add error tag")
        return
    stash_api.add_tags_to_images(image_ids, [AI_Error_Tag_Id])

def get_ai_tag_ids_from_names(tag_names: list[str]) -> list[int]:
    """Get tag IDs for the given tag names, creating them if necessary."""
    return [stash_api.fetch_tag_id(tag, parent_id=AI_Base_Tag_Id, create_if_missing=True, add_to_cache=AI_tags_cache) for tag in tag_names]