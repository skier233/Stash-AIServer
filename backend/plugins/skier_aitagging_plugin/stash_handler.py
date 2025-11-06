import logging
from stash_ai_server.utils.stash_api import stash_api

_log = logging.getLogger(__name__)

AI_Base_Tag_Name = "AI"
AI_Base_Tag_Id = stash_api.fetch_tag_id(AI_Base_Tag_Name, create_if_missing=True)

AI_Error_Tag_Name = "AI_Errored"

AI_Tagged_Tag_Name = "AI_Tagged"

VR_TAG_NAME = stash_api.stash_interface.get_configuration()["ui"].get("vrTag", None)
VR_Tag_Id = stash_api.fetch_tag_id(VR_TAG_NAME) if VR_TAG_NAME else None
AI_Error_Tag_Id = stash_api.fetch_tag_id(AI_Error_Tag_Name, parent_id=AI_Base_Tag_Id, create_if_missing=True)
AI_Tagged_Tag_Id = stash_api.fetch_tag_id(AI_Tagged_Tag_Name, create_if_missing=True)

#TODO: could be nice to not have to rely on the parent logic
AI_tags_cache = stash_api.get_tags_with_parent(parent_tag_id=AI_Base_Tag_Id)

AI_tags_cache[AI_Error_Tag_Name] = AI_Error_Tag_Id

def has_ai_tagged(tags: list[int]) -> bool:
    """Check if the scene has the AI_Tagged tag."""
    global AI_Tagged_Tag_Id
    return AI_Tagged_Tag_Id in tags if AI_Tagged_Tag_Id else False

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


def resolve_ai_tag_reference(label: str) -> int | None:
    """Resolve (and ensure) the Stash tag id for a label used in AI results."""
    if not label:
        return None
    try:
        return stash_api.fetch_tag_id(
            label,
            parent_id=AI_Base_Tag_Id,
            create_if_missing=True,
            add_to_cache=AI_tags_cache,
        )
    except Exception:
        _log.exception("Failed to resolve AI tag reference for label=%s", label)
        return None

def is_vr_scene(tag_ids: list[int]) -> bool:
    """Check if the scene is tagged as VR."""
    global VR_Tag_Id
    return VR_Tag_Id in tag_ids if VR_Tag_Id else False