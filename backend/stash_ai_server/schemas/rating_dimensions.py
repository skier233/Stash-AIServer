"""Defines the available rating dimensions per entity type.

Each dimension maps to a ``rating_key`` stored in :class:`EntityRating`.
The ``default`` key is always implicitly available and represents the
overall rating.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RatingDimension:
    key: str
    label: str
    description: str
    icon: str  # short emoji/symbol hint for UI


# -- Scene-specific rating dimensions ----------------------------------------

SCENE_DIMENSIONS: list[RatingDimension] = [
    RatingDimension(
        key="default",
        label="Overall",
        description="Overall scene rating",
        icon="",
    ),
    RatingDimension(
        key="performers",
        label="Performers",
        description="Quality and appeal of performers in this scene",
        icon="",
    ),
    RatingDimension(
        key="content",
        label="Content",
        description="Quality of the action and content taking place",
        icon="",
    ),
    RatingDimension(
        key="video_quality",
        label="Video",
        description="Video quality, cinematography, lighting, and framing",
        icon="",
    ),
    RatingDimension(
        key="audio",
        label="Audio",
        description="Audio quality and appeal of the scene",
        icon="",
    ),
]

# -- Registry mapping entity_type -> dimensions ------------------------------

DIMENSION_REGISTRY: dict[str, list[RatingDimension]] = {
    "scene": SCENE_DIMENSIONS,
}


def get_dimensions(entity_type: str) -> list[RatingDimension]:
    """Return the rating dimensions for *entity_type*, defaulting to just
    the ``default`` dimension if the type has no custom dimensions."""
    return DIMENSION_REGISTRY.get(entity_type, [SCENE_DIMENSIONS[0]])
