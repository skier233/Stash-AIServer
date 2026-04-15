"""Training / Staging Ground API.

Provides endpoints for the explicit feedback loop:
  - Serve scenes for the user to rate (sampled from clusters or random)
  - Track training session state
  - Trigger taste profile recomputation after training
"""
from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional, Set

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from stash_ai_server.core.api_key import require_shared_api_key
from stash_ai_server.db.session import get_session_local
from stash_ai_server.models.entity_embeddings import EntityEmbedding
from stash_ai_server.models.ratings import EntityRating
from stash_ai_server.models.taste_profile import ContentCluster, ContentClusterMember
from stash_ai_server.recommendations.registry import recommender_registry
from stash_ai_server.recommendations.models import RecContext
from stash_ai_server.recommendations.utils.scene_fetch import fetch_scenes_by_ids
from stash_ai_server.recommendations.utils.watch_history import load_watch_history_summary
from stash_ai_server.services.taste_compute import (
    compute_and_store_centroids,
    compute_and_store_profile,
)

_log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/training",
    tags=["training"],
    dependencies=[Depends(require_shared_api_key)],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class TrainingBatchRequest(BaseModel):
    strategy: str = Field("mixed", description="Sampling strategy: 'mixed', 'random', 'cluster', 'uncertain'")
    batch_size: int = Field(12, ge=4, le=50)
    cluster_id: Optional[int] = Field(None, description="Specific cluster for 'cluster' strategy")
    exclude_rated: bool = Field(True, description="Exclude scenes the user already rated")
    exclude_tag_names: Optional[List[str]] = Field(None, description="Tag names to exclude (scenes with ANY of these are removed)")
    include_tag_names: Optional[List[str]] = Field(None, description="Tag names to require (scenes must have at least one)")


class QuickRateRequest(BaseModel):
    scene_id: int
    rating: int = Field(..., ge=0, le=100, description="0=terrible, 50=neutral, 100=excellent")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_rated_scene_ids() -> Set[int]:
    """Return scene IDs that have explicit ratings (AI custom or Stash native)."""
    rated: Set[int] = set()

    # AI custom ratings (entity_ratings table)
    try:
        with get_session_local()() as session:
            rows = session.execute(
                sa.select(EntityRating.entity_id).where(
                    EntityRating.entity_type == "scene",
                    EntityRating.rating_key == "default",
                )
            ).scalars().all()
            rated.update(int(r) for r in rows)
    except Exception:
        pass

    # Stash native ratings (scenes table in Stash DB)
    try:
        from stash_ai_server.utils.stash_db import get_stash_session_factory
        factory = get_stash_session_factory()
        if factory:
            with factory() as session:
                scenes_table = sa.Table(
                    "scenes", sa.MetaData(),
                    autoload_with=session.bind,
                )
                rating_col = None
                for col in scenes_table.columns:
                    if col.name == "rating":
                        rating_col = col
                        break
                if rating_col is not None:
                    rows = session.execute(
                        sa.select(scenes_table.c.id).where(
                            rating_col.isnot(None),
                            rating_col != 0,
                        )
                    ).scalars().all()
                    rated.update(int(r) for r in rows)
    except Exception:
        _log.debug("Could not check Stash native ratings", exc_info=True)

    return rated


def _get_watched_scene_ids(limit: int = 500) -> Set[int]:
    """Return set of scene IDs the user has watched."""
    history = load_watch_history_summary(min_watch_seconds=10, limit=limit)
    return {e["scene_id"] for e in history}


def _get_scenes_with_embeddings(limit: int = 2000) -> List[int]:
    """Return scene IDs that have visual embeddings."""
    try:
        with get_session_local()() as session:
            rows = session.execute(
                sa.select(EntityEmbedding.entity_id)
                .where(
                    EntityEmbedding.embedding_type.like("visual_metaclip2%"),
                    EntityEmbedding.entity_type == "scene",
                )
                .distinct()
                .limit(limit)
            ).scalars().all()
            return list(rows)
    except Exception:
        return []


def _sample_from_clusters(
    n: int,
    exclude: Set[int],
    cluster_id: Optional[int] = None,
) -> List[int]:
    """Sample scene IDs from content clusters."""
    try:
        with get_session_local()() as session:
            q = sa.select(ContentClusterMember.scene_id)
            if cluster_id is not None:
                q = q.where(ContentClusterMember.cluster_id == cluster_id)
            rows = session.execute(q).scalars().all()
            candidates = [r for r in rows if r not in exclude]
            if not candidates:
                return []
            random.shuffle(candidates)
            return candidates[:n]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/status")
async def training_status():
    """Return current training stats — rated count, watched count, etc."""
    rated = _get_rated_scene_ids()
    watched = _get_watched_scene_ids()
    embedded = _get_scenes_with_embeddings()

    # Cluster info
    clusters = []
    try:
        with get_session_local()() as session:
            rows = session.execute(
                sa.select(ContentCluster).order_by(ContentCluster.scene_count.desc())
            ).scalars().all()
            clusters = [
                {
                    "id": r.id,
                    "label": r.cluster_label,
                    "scene_count": r.scene_count,
                    "avg_engagement": r.avg_engagement,
                    "user_affinity": r.user_affinity,
                }
                for r in rows
            ]
    except Exception:
        pass

    return {
        "rated_count": len(rated),
        "watched_count": len(watched),
        "embedded_count": len(embedded),
        "clusters": clusters,
        "recommenders": [
            {"id": d.id, "label": d.label}
            for d in recommender_registry.list_for_context(RecContext.global_feed)
        ],
    }


@router.post("/batch")
async def get_training_batch(body: TrainingBatchRequest):
    """Get a batch of scenes for the user to rate.

    Strategies:
      - mixed: balanced from watched-but-unrated + clusters + random
      - random: random scenes with embeddings
      - cluster: from a specific content cluster
      - uncertain: scenes the system is least sure about
    """
    exclude = _get_rated_scene_ids() if body.exclude_rated else set()
    n = body.batch_size
    selected: List[int] = []

    if body.strategy == "cluster":
        selected = _sample_from_clusters(n, exclude, cluster_id=body.cluster_id)

    elif body.strategy == "random":
        all_embedded = _get_scenes_with_embeddings()
        candidates = [s for s in all_embedded if s not in exclude]
        random.shuffle(candidates)
        selected = candidates[:n]

    elif body.strategy == "uncertain":
        # Scenes the user watched but engagement is middling (p40-p60)
        from stash_ai_server.recommendations.engagement.scorer import score_all_watched_scenes
        try:
            results = score_all_watched_scenes(limit=500)
            scores = sorted(results, key=lambda r: r.score)
            # Middle band
            lo = int(len(scores) * 0.35)
            hi = int(len(scores) * 0.65)
            mid = scores[lo:hi]
            candidates = [r.entity_id for r in mid if r.entity_id not in exclude]
            random.shuffle(candidates)
            selected = candidates[:n]
        except Exception:
            _log.debug("uncertain strategy: fallback to random")
            all_embedded = _get_scenes_with_embeddings()
            candidates = [s for s in all_embedded if s not in exclude]
            random.shuffle(candidates)
            selected = candidates[:n]

    else:  # "mixed"
        # 1/3 from watched-but-unrated, 1/3 from clusters, 1/3 random
        watched = _get_watched_scene_ids()
        watched_unrated = [s for s in watched if s not in exclude]
        random.shuffle(watched_unrated)
        batch_a = watched_unrated[:n // 3]

        batch_b = _sample_from_clusters(n // 3, exclude | set(batch_a))

        all_embedded = _get_scenes_with_embeddings()
        used = exclude | set(batch_a) | set(batch_b)
        random_pool = [s for s in all_embedded if s not in used]
        random.shuffle(random_pool)
        batch_c = random_pool[:n - len(batch_a) - len(batch_b)]

        selected = batch_a + batch_b + batch_c
        random.shuffle(selected)

    if not selected:
        return {"scenes": [], "total": 0, "rated_ids": []}

    # Apply tag filtering (cross-cutting, works for all strategies)
    if body.exclude_tag_names or body.include_tag_names:
        from stash_ai_server.api.tags import resolve_and_filter_scene_ids
        selected = resolve_and_filter_scene_ids(
            selected,
            exclude_tag_names=body.exclude_tag_names,
            include_tag_names=body.include_tag_names,
        )
        if not selected:
            return {"scenes": [], "total": 0, "rated_ids": []}

    # Hydrate
    payloads = fetch_scenes_by_ids(selected)

    # Check which of the selected scenes already have ratings
    all_rated = _get_rated_scene_ids()

    scenes = []
    for sid in selected:
        p = payloads.get(sid)
        if p:
            p["training_source"] = body.strategy
            # Include whether this scene has a rating from Stash or AI
            p["has_rating"] = sid in all_rated or (p.get("rating100") is not None and p["rating100"] > 0)
            scenes.append(p)

    rated_ids = [s["id"] for s in scenes if s.get("has_rating")]
    return {"scenes": scenes, "total": len(scenes), "rated_ids": rated_ids}


@router.post("/rate")
async def quick_rate(body: QuickRateRequest):
    """Quick-rate a scene during training (upserts default rating)."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    with get_session_local()() as session:
        stmt = (
            pg_insert(EntityRating)
            .values(
                entity_type="scene",
                entity_id=str(body.scene_id),
                rating_key="default",
                value=body.rating,
            )
            .on_conflict_do_update(
                constraint="uq_entity_rating",
                set_={
                    "value": body.rating,
                    "updated_at": sa.text("now()"),
                },
            )
        )
        session.execute(stmt)
        session.commit()

    return {"status": "ok", "scene_id": body.scene_id, "rating": body.rating}


class CheckRatedRequest(BaseModel):
    scene_ids: List[int] = Field(..., description="Scene IDs to check for ratings")


@router.post("/check-rated")
async def check_rated(body: CheckRatedRequest):
    """Check which of the given scene IDs have been rated (AI or Stash native).

    Called when the user returns to the training page after rating scenes
    on their detail pages.
    """
    all_rated = _get_rated_scene_ids()
    rated_ids = [sid for sid in body.scene_ids if sid in all_rated]
    return {"rated_ids": rated_ids}


@router.post("/finalize")
async def finalize_training():
    """After a training session, recompute profile and centroids."""
    profile = compute_and_store_profile(profile_type="global")
    centroids = compute_and_store_centroids()
    return {
        "status": "ok",
        "profile_watched": profile.get("watched_scenes", 0),
        "centroids": centroids,
    }
