"""Taste Profile API — exposes the system's understanding of user preferences.

Endpoints:
  GET  /summary            — return cached profile or compute fresh
  POST /recompute          — force full recompute (profile + centroids)
  GET  /centroids          — return cached embedding centroids
  GET  /clusters           — return cached content clusters
  POST /clusters/recompute — recompute content clusters
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, Query

from stash_ai_server.core.api_key import require_shared_api_key
from stash_ai_server.services.taste_compute import (
    compute_and_store_centroids,
    compute_and_store_clusters,
    compute_and_store_profile,
    get_cached_centroids,
    get_cached_clusters,
    get_cached_profile,
    get_cluster_scene_ids,
    get_centroid_scene_ids,
)

_log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/taste-profile",
    tags=["taste-profile"],
    dependencies=[Depends(require_shared_api_key)],
)

_EMPTY_PROFILE: Dict[str, Any] = {
    "watched_scenes": 0,
    "liked_scenes": 0,
    "disliked_scenes": 0,
    "corpus_size": 0,
    "engagement_stats": {},
    "tags": [],
    "negative_tags": [],
    "performers": [],
    "negative_performers": [],
    "embedding_stats": {},
}


@router.get("/summary")
async def get_taste_summary(
    history_limit: int = Query(400, ge=25, le=2000),
    min_watch_seconds: float = Query(15.0, ge=0, le=600),
    recency_half_life: float = Query(30.0, ge=0, le=365),
    top_tags: int = Query(40, ge=5, le=200),
    top_performers: int = Query(20, ge=5, le=100),
    force: bool = Query(False),
):
    """Return the user's taste profile.

    Serves from cache if available, otherwise computes fresh.
    Pass ``force=true`` to always recompute.
    """
    if not force:
        cached = get_cached_profile("global")
        if cached:
            return cached

    result = compute_and_store_profile(
        profile_type="global",
        history_limit=history_limit,
        min_watch_seconds=min_watch_seconds,
        recency_half_life=recency_half_life,
        top_tags=top_tags,
        top_performers=top_performers,
    )
    if not result or result.get("watched_scenes", 0) == 0:
        return _EMPTY_PROFILE
    return result


@router.post("/recompute")
async def recompute_profile(
    history_limit: int = Query(400, ge=25, le=2000),
    min_watch_seconds: float = Query(15.0, ge=0, le=600),
    recency_half_life: float = Query(30.0, ge=0, le=365),
    top_tags: int = Query(60, ge=5, le=200),
    top_performers: int = Query(40, ge=5, le=100),
):
    """Force recompute of taste profile AND centroids."""
    profile = compute_and_store_profile(
        profile_type="global",
        history_limit=history_limit,
        min_watch_seconds=min_watch_seconds,
        recency_half_life=recency_half_life,
        top_tags=top_tags,
        top_performers=top_performers,
    )

    centroids = compute_and_store_centroids(
        history_limit=history_limit,
        min_watch_seconds=min_watch_seconds,
    )

    return {
        "profile": profile,
        "centroids": centroids,
    }


@router.get("/centroids")
async def get_centroids():
    """Return cached embedding taste centroids."""
    return {"centroids": get_cached_centroids()}


@router.get("/clusters")
async def get_clusters():
    """Return cached content clusters."""
    return {"clusters": get_cached_clusters()}


@router.post("/clusters/recompute")
async def recompute_clusters(
    n_clusters: int = Query(20, ge=5, le=100),
    min_scenes: int = Query(3, ge=1, le=20),
):
    """Recompute content clusters from scene embeddings."""
    result = compute_and_store_clusters(
        n_clusters=n_clusters,
        min_scenes_per_cluster=min_scenes,
    )
    return result


@router.get("/clusters/{cluster_id}/scenes")
async def get_cluster_scenes(
    cluster_id: int,
    limit: int = Query(50, ge=1, le=200),
):
    """Return scene IDs belonging to a specific content cluster."""
    scene_ids = get_cluster_scene_ids(cluster_id, limit=limit)
    return {"cluster_id": cluster_id, "scene_ids": scene_ids}


@router.get("/centroids/scenes")
async def get_centroid_scenes(
    centroid_type: str = Query("liked_0"),
    embedding_type: str = Query("visual_metaclip2"),
    limit: int = Query(30, ge=1, le=100),
):
    """Return scene IDs closest to a specific taste centroid."""
    scene_ids = get_centroid_scene_ids(
        centroid_type=centroid_type,
        embedding_type=embedding_type,
        limit=limit,
    )
    return {
        "centroid_type": centroid_type,
        "embedding_type": embedding_type,
        "scene_ids": scene_ids,
    }
