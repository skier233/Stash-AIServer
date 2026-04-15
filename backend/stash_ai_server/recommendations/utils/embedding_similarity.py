"""Embedding-based similarity utilities for the recommendation system.

Wraps the low-level ``embedding_store`` functions into recommendation-friendly
helpers that work with sets of scene IDs and return ``{scene_id: similarity}``
maps ready to be blended with tag/performer/studio scores.

Embedding types handled:
  - ``visual_metaclip2_section_N`` (768-d, semantic scene content)
  - ``visual_dinov3_section_N``    (768-d, visual style/appearance)
  - ``audio_speech`` / ``audio_moan`` / ``audio_breath`` (192-d, ECAPA-TDNN)

All functions gracefully return empty dicts when no embeddings are available.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List, Mapping, Sequence, Set, Tuple

import numpy as np
import sqlalchemy as sa
from sqlalchemy import select

from stash_ai_server.db.embedding_store import (
    SimilarEntity,
    find_similar_entities,
)
from stash_ai_server.db.session import get_session_local
from stash_ai_server.models.entity_embeddings import EntityEmbedding

_log = logging.getLogger(__name__)

# Embedding type prefixes we care about for recommendations.
VISUAL_PREFIX = "visual_metaclip2"
VISUAL_DINOV3_PREFIX = "visual_dinov3"
AUDIO_PREFIXES = ("audio_speech", "audio_moan", "audio_breath")

# Limits
DEFAULT_LIMIT_PER_QUERY = 150
MAX_TASTE_REFERENCE_SCENES = 25


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _fetch_embeddings_by_prefix(
    entity_ids: Sequence[int],
    type_prefix: str,
    entity_type: str = "scene",
) -> Dict[str, List[Tuple[int, List[float]]]]:
    """Batch-fetch embeddings for multiple entities matching a type prefix.

    Returns ``{embedding_type: [(entity_id, vector), ...]}`` grouped by the
    full embedding type string (e.g. ``visual_metaclip2_section_0``).
    """
    if not entity_ids:
        return {}

    result: Dict[str, List[Tuple[int, List[float]]]] = defaultdict(list)

    with get_session_local()() as session:
        q = (
            select(
                EntityEmbedding.entity_id,
                EntityEmbedding.embedding_type,
                EntityEmbedding.embedding,
            )
            .where(
                EntityEmbedding.entity_type == entity_type,
                EntityEmbedding.entity_id.in_(list(entity_ids)),
                EntityEmbedding.embedding_type.like(type_prefix + "%"),
            )
        )
        for row in session.execute(q):
            vec = list(row.embedding) if row.embedding is not None else []
            if vec:
                result[row.embedding_type].append((int(row.entity_id), vec))

    return dict(result)


def _distance_to_similarity(distance: float) -> float:
    """Convert pgvector cosine distance to a [0, 1] similarity score.

    Cosine distance ∈ [0, 2]:  0 = identical, 1 = orthogonal, 2 = opposite.
    """
    return max(0.0, 1.0 - distance)


# ---------------------------------------------------------------------------
# Similar-scene embedding search
# ---------------------------------------------------------------------------

def find_similar_by_seed_embeddings(
    seed_scene_ids: Sequence[int],
    type_prefix: str = VISUAL_PREFIX,
    limit_per_query: int = DEFAULT_LIMIT_PER_QUERY,
    exclude_scene_ids: Set[int] | None = None,
) -> Dict[int, float]:
    """Find scenes visually/aurally similar to one or more seed scenes.

    For each embedding of each seed scene (matching *type_prefix*), performs a
    pgvector cosine search.  Results are merged by keeping the **best**
    (highest) similarity per candidate.

    Returns ``{scene_id: similarity}`` where similarity ∈ [0, 1].
    """
    all_exclude = set(seed_scene_ids)
    if exclude_scene_ids:
        all_exclude.update(exclude_scene_ids)

    grouped = _fetch_embeddings_by_prefix(seed_scene_ids, type_prefix)
    if not grouped:
        return {}

    best_sim: Dict[int, float] = {}
    queries = 0

    for emb_type, entries in grouped.items():
        for _eid, vector in entries:
            results = find_similar_entities(
                embedding_type=emb_type,
                query_vector=vector,
                limit=limit_per_query,
                entity_type="scene",
            )
            queries += 1
            for r in results:
                if r.entity_id in all_exclude:
                    continue
                sim = _distance_to_similarity(r.distance)
                if sim > best_sim.get(r.entity_id, 0.0):
                    best_sim[r.entity_id] = sim

    _log.debug(
        "embedding seed search: %d queries, %d candidates from %d seeds (%s*)",
        queries, len(best_sim), len(seed_scene_ids), type_prefix,
    )
    return best_sim


# ---------------------------------------------------------------------------
# Global-feed taste centroid search
# ---------------------------------------------------------------------------

def find_similar_by_taste_centroid(
    watched_scene_ids: Sequence[int],
    engagement_scores: Mapping[int, float] | None = None,
    type_prefix: str = VISUAL_PREFIX,
    limit_per_query: int = DEFAULT_LIMIT_PER_QUERY,
    max_reference_scenes: int = MAX_TASTE_REFERENCE_SCENES,
) -> Dict[int, float]:
    """Build an embedding taste centroid from watched scenes and search.

    1. Select the top-engaged watched scenes (up to *max_reference_scenes*).
    2. For each embedding type matching *type_prefix*, compute the
       engagement-weighted centroid (average) of all matching embeddings.
    3. Use each centroid to search for similar scenes via pgvector.
    4. Merge results keeping the best similarity per candidate.

    Returns ``{scene_id: similarity}`` where similarity ∈ [0, 1].
    """
    # Fetch embeddings for ALL watched scenes first, then rank by engagement.
    # This avoids the case where the top-engaged scenes have no embeddings
    # even though other watched scenes do.
    exclude_set = set(watched_scene_ids)
    grouped = _fetch_embeddings_by_prefix(list(watched_scene_ids), type_prefix)
    if not grouped:
        return {}

    # Identify which watched scenes actually have embeddings
    scenes_with_embeds: Set[int] = set()
    for entries in grouped.values():
        for eid, _ in entries:
            scenes_with_embeds.add(eid)

    # If more than the limit, keep the top-engaged ones
    if len(scenes_with_embeds) > max_reference_scenes:
        ranked = sorted(
            scenes_with_embeds,
            key=lambda s: engagement_scores.get(s, 0.0) if engagement_scores else 0.0,
            reverse=True,
        )
        keep = set(ranked[:max_reference_scenes])
        grouped = {
            emb_type: [(eid, vec) for eid, vec in entries if eid in keep]
            for emb_type, entries in grouped.items()
        }
        grouped = {k: v for k, v in grouped.items() if v}

    best_sim: Dict[int, float] = {}
    queries = 0

    for emb_type, entries in grouped.items():
        if not entries:
            continue

        # Compute engagement-weighted centroid
        vectors = np.array([v for _, v in entries])
        weights = np.ones(len(entries))
        if engagement_scores:
            weights = np.array([
                max(engagement_scores.get(eid, 0.0), 0.01)
                for eid, _ in entries
            ])
        weights = weights / weights.sum()
        centroid = (vectors * weights[:, np.newaxis]).sum(axis=0)

        # L2 normalize
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm

        results = find_similar_entities(
            embedding_type=emb_type,
            query_vector=centroid.tolist(),
            limit=limit_per_query,
            entity_type="scene",
        )
        queries += 1
        for r in results:
            if r.entity_id in exclude_set:
                continue
            sim = _distance_to_similarity(r.distance)
            if sim > best_sim.get(r.entity_id, 0.0):
                best_sim[r.entity_id] = sim

    _log.debug(
        "embedding taste centroid: %d queries, %d candidates from %d refs (%s*)",
        queries, len(best_sim), len(scenes_with_embeds), type_prefix,
    )
    return best_sim


# ---------------------------------------------------------------------------
# Multi-type aggregation
# ---------------------------------------------------------------------------

def compute_embedding_similarity(
    *,
    scene_ids: Sequence[int] | None = None,
    watched_scene_ids: Sequence[int] | None = None,
    engagement_scores: Mapping[int, float] | None = None,
    exclude_scene_ids: Set[int] | None = None,
    mode: str = "seed",
    visual_prefix: str = VISUAL_PREFIX,
    include_audio: bool = True,
    limit_per_query: int = DEFAULT_LIMIT_PER_QUERY,
) -> Dict[int, float]:
    """High-level entry point combining visual and audio embedding similarity.

    Parameters
    ----------
    scene_ids : list[int] | None
        Seed scene IDs (for ``mode="seed"`` / similar_scene).
    watched_scene_ids : list[int] | None
        Watched scene IDs (for ``mode="taste"`` / global_feed).
    engagement_scores : dict | None
        Engagement scores for weighting taste centroids.
    exclude_scene_ids : set | None
        Scene IDs to exclude from results.
    mode : str
        ``"seed"`` for similar_scene, ``"taste"`` for global_feed.
    visual_prefix : str
        Visual embedding type prefix to use.
    include_audio : bool
        Whether to also search audio embeddings.
    limit_per_query : int
        Max results per pgvector query.

    Returns
    -------
    dict[int, float]
        ``{scene_id: similarity}`` where similarity ∈ [0, 1].
        Aggregated across visual and audio, keeping best per scene.
    """
    combined: Dict[int, float] = {}

    prefixes = [visual_prefix]
    # Always include dinov3 for visual-to-visual similarity alongside the
    # primary visual prefix (metaclip2 handles visual-text alignment).
    if visual_prefix == VISUAL_PREFIX:
        prefixes.append(VISUAL_DINOV3_PREFIX)
    if include_audio:
        prefixes.extend(AUDIO_PREFIXES)

    for prefix in prefixes:
        if mode == "seed" and scene_ids:
            sim_map = find_similar_by_seed_embeddings(
                seed_scene_ids=scene_ids,
                type_prefix=prefix,
                limit_per_query=limit_per_query,
                exclude_scene_ids=exclude_scene_ids,
            )
        elif mode == "taste" and watched_scene_ids:
            sim_map = find_similar_by_taste_centroid(
                watched_scene_ids=watched_scene_ids,
                engagement_scores=engagement_scores,
                type_prefix=prefix,
                limit_per_query=limit_per_query,
            )
        else:
            continue

        for sid, sim in sim_map.items():
            if sim > combined.get(sid, 0.0):
                combined[sid] = sim

    if combined:
        _log.info(
            "embedding similarity: %d candidates, mode=%s, prefixes=%s",
            len(combined), mode, prefixes,
        )
    return combined


# ---------------------------------------------------------------------------
# Cached-centroid taste search (uses precomputed taste_centroids table)
# ---------------------------------------------------------------------------

def find_similar_by_cached_centroids(
    *,
    exclude_scene_ids: Set[int] | None = None,
    limit_per_query: int = DEFAULT_LIMIT_PER_QUERY,
    type_prefix: str | None = None,
) -> Dict[int, float]:
    """Search for similar scenes using precomputed taste centroids.

    Reads the ``liked`` centroid(s) from the ``taste_centroids`` table
    (populated by ``taste_compute.compute_and_store_centroids``).
    Falls back gracefully to an empty dict if no centroids are stored.

    If *type_prefix* is given, only centroids whose ``embedding_type``
    matches that prefix are used (e.g. ``"visual_dinov3"``).

    Returns ``{scene_id: similarity}`` where similarity in [0, 1].
    """
    try:
        from stash_ai_server.models.taste_profile import TasteCentroid
    except ImportError:
        return {}

    exclude = set(exclude_scene_ids) if exclude_scene_ids else set()
    best_sim: Dict[int, float] = {}

    try:
        with get_session_local()() as session:
            # Match "liked", "liked_0", "liked_1", etc.
            q = select(TasteCentroid).where(TasteCentroid.centroid_type.like("liked%"))
            if type_prefix:
                q = q.where(TasteCentroid.embedding_type == type_prefix)
            rows = session.execute(q).scalars().all()

        if not rows:
            _log.debug("cached centroids: no liked centroids found")
            return {}

        queries = 0
        for row in rows:
            centroid_vec = list(row.centroid) if row.centroid is not None else []
            if not centroid_vec:
                continue

            # Map embedding_type back to a section query type
            # The centroid was built from all sections; search section_0 for speed
            emb_type = f"{row.embedding_type}_section_0"

            results = find_similar_entities(
                embedding_type=emb_type,
                query_vector=centroid_vec,
                limit=limit_per_query,
                entity_type="scene",
            )
            queries += 1
            for r in results:
                if r.entity_id in exclude:
                    continue
                sim = _distance_to_similarity(r.distance)
                if sim > best_sim.get(r.entity_id, 0.0):
                    best_sim[r.entity_id] = sim

        _log.info(
            "cached centroid search: %d queries, %d candidates",
            queries, len(best_sim),
        )
    except Exception:
        _log.debug("cached centroid search failed", exc_info=True)

    return best_sim
