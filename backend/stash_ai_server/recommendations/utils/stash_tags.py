"""Stash scene-level tag utilities for content-based recommendations.

Works exclusively with Stash DB tags (the tags every user has) rather than
AI-detected tags.  Provides:

- Bulk tag fetching for scenes (scene_id -> set of tag_ids)
- Corpus-level document frequency and IDF computation
- TF-IDF vector construction (binary TF since Stash tags are present/absent)
- User taste profile construction weighted by engagement scores
- Tag name lookup
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Mapping, Set, Sequence, Tuple

import sqlalchemy as sa

from stash_ai_server.utils import stash_db

_log = logging.getLogger(__name__)

# Tag names that should be excluded from content-based similarity scoring.
# These are metadata/workflow tags that don't describe actual scene content.
DEFAULT_BLACKLISTED_TAG_NAMES: List[str] = [
    "AI_Tagged",
    "AI_Errored",
]


# ---------------------------------------------------------------------------
# Low-level Stash DB access
# ---------------------------------------------------------------------------

def _get_tag_tables() -> Tuple[sa.Table | None, sa.Table | None]:
    """Return (tag_link_table, tags_table) or (None, None) if unavailable."""
    tag_link = stash_db.get_first_available_table(
        "scene_tags", "scenes_tags", "tags_scenes",
        required_columns=("scene_id", "tag_id"),
    )
    tags_table = stash_db.get_stash_table("tags", required=False)
    if tag_link is None or tags_table is None:
        return None, None
    if tag_link.c.get("scene_id") is None or tag_link.c.get("tag_id") is None:
        return None, None
    if tags_table.c.get("id") is None or tags_table.c.get("name") is None:
        return None, None
    return tag_link, tags_table


def resolve_tag_ids_by_name(
    names: Sequence[str],
) -> Dict[str, int]:
    """Look up tag IDs by name.  Returns ``{tag_name: tag_id}`` for found tags.

    Names that don't exist in the Stash DB are silently omitted.
    """
    if not names:
        return {}
    session_factory = stash_db.get_stash_sessionmaker()
    _, tags_table = _get_tag_tables()
    if session_factory is None or tags_table is None:
        return {}

    stmt = sa.select(
        tags_table.c.get("id").label("id"),
        tags_table.c.get("name").label("name"),
    ).where(tags_table.c.get("name").in_(list(names)))

    lookup: Dict[str, int] = {}
    with session_factory() as session:
        for row in session.execute(stmt):
            try:
                lookup[str(row.name)] = int(row.id)
            except (TypeError, ValueError):
                continue
    return lookup


def resolve_blacklisted_tag_ids(
    extra_names: Sequence[str] | None = None,
) -> Set[int]:
    """Resolve the default + optional extra blacklisted tag names to IDs.

    Returns a set of tag IDs to exclude from content-based scoring.
    """
    names = list(DEFAULT_BLACKLISTED_TAG_NAMES)
    if extra_names:
        names.extend(extra_names)
    name_map = resolve_tag_ids_by_name(names)
    ids = set(name_map.values())
    if ids:
        _log.debug("content tags blacklist resolved: %s", {n: i for n, i in name_map.items()})
    return ids


def fetch_scene_tag_ids(
    scene_ids: Sequence[int],
    exclude_tag_ids: Set[int] | None = None,
) -> Dict[int, Set[int]]:
    """Return ``{scene_id: {tag_id, ...}}`` for the requested scenes.

    Scenes with no tags (after exclusion) are omitted from the result.
    """
    normalized = [int(sid) for sid in scene_ids if sid is not None]
    if not normalized:
        return {}

    session_factory = stash_db.get_stash_sessionmaker()
    tag_link, _ = _get_tag_tables()
    if session_factory is None or tag_link is None:
        return {}

    scene_col = tag_link.c.get("scene_id")
    tag_col = tag_link.c.get("tag_id")

    stmt = sa.select(scene_col.label("scene_id"), tag_col.label("tag_id")).where(
        scene_col.in_(normalized)
    )

    result: Dict[int, Set[int]] = defaultdict(set)
    with session_factory() as session:
        for row in session.execute(stmt):
            try:
                tid = int(row.tag_id)
                if exclude_tag_ids and tid in exclude_tag_ids:
                    continue
                result[int(row.scene_id)].add(tid)
            except (TypeError, ValueError):
                continue
    return dict(result)


def fetch_all_scene_tag_ids(
    exclude_tag_ids: Set[int] | None = None,
) -> Dict[int, Set[int]]:
    """Return ``{scene_id: {tag_id, ...}}`` for the entire Stash library.

    Used to build the corpus-level IDF index.  Scenes with no tags (after
    exclusion) are omitted.
    """
    session_factory = stash_db.get_stash_sessionmaker()
    tag_link, _ = _get_tag_tables()
    if session_factory is None or tag_link is None:
        return {}

    scene_col = tag_link.c.get("scene_id")
    tag_col = tag_link.c.get("tag_id")

    stmt = sa.select(scene_col.label("scene_id"), tag_col.label("tag_id"))

    result: Dict[int, Set[int]] = defaultdict(set)
    with session_factory() as session:
        for row in session.execute(stmt):
            try:
                tid = int(row.tag_id)
                if exclude_tag_ids and tid in exclude_tag_ids:
                    continue
                result[int(row.scene_id)].add(tid)
            except (TypeError, ValueError):
                continue
    return dict(result)


def fetch_tag_names(tag_ids: Iterable[int]) -> Dict[int, str]:
    """Return ``{tag_id: tag_name}`` for the requested tags."""
    tag_list = [int(tid) for tid in tag_ids if tid is not None]
    if not tag_list:
        return {}

    session_factory = stash_db.get_stash_sessionmaker()
    _, tags_table = _get_tag_tables()
    if session_factory is None or tags_table is None:
        return {}

    stmt = sa.select(
        tags_table.c.get("id").label("id"),
        tags_table.c.get("name").label("name"),
    ).where(tags_table.c.get("id").in_(tag_list))

    lookup: Dict[int, str] = {}
    with session_factory() as session:
        for row in session.execute(stmt):
            try:
                lookup[int(row.id)] = str(row.name)
            except (TypeError, ValueError):
                continue
    return lookup


def fetch_all_tag_names() -> Dict[int, str]:
    """Return ``{tag_id: tag_name}`` for all tags in the Stash DB."""
    session_factory = stash_db.get_stash_sessionmaker()
    _, tags_table = _get_tag_tables()
    if session_factory is None or tags_table is None:
        return {}

    stmt = sa.select(
        tags_table.c.get("id").label("id"),
        tags_table.c.get("name").label("name"),
    )

    lookup: Dict[int, str] = {}
    with session_factory() as session:
        for row in session.execute(stmt):
            try:
                lookup[int(row.id)] = str(row.name)
            except (TypeError, ValueError):
                continue
    return lookup


# ---------------------------------------------------------------------------
# Corpus statistics and IDF
# ---------------------------------------------------------------------------

def compute_document_frequencies(
    corpus: Mapping[int, Set[int]],
) -> Tuple[Dict[int, int], int]:
    """Compute per-tag document frequency from the full corpus.

    Returns ``(df_map, total_docs)`` where ``df_map[tag_id]`` is the number of
    scenes containing that tag and ``total_docs`` is the corpus size.
    """
    df: Dict[int, int] = defaultdict(int)
    for tag_set in corpus.values():
        for tag_id in tag_set:
            df[tag_id] += 1
    return dict(df), len(corpus)


def compute_idf(
    df_map: Mapping[int, int],
    total_docs: int,
) -> Dict[int, float]:
    """Compute smoothed IDF weights: ``log((1 + N) / (1 + df)) + 1``.

    Tags appearing in every scene get a low but positive weight; rare tags are
    boosted.
    """
    n = max(total_docs, 1)
    return {
        tag_id: math.log((1.0 + n) / (1.0 + df)) + 1.0
        for tag_id, df in df_map.items()
    }


# ---------------------------------------------------------------------------
# TF-IDF vector construction
# ---------------------------------------------------------------------------

def build_tfidf_vector(
    tag_ids: Set[int],
    idf: Mapping[int, float],
) -> Dict[int, float]:
    """Build an L2-normalized TF-IDF vector for a single scene.

    TF is binary (1 if tag present). The resulting vector is normalized to unit
    length so cosine similarity reduces to a dot product.
    """
    raw = {tid: idf.get(tid, 1.0) for tid in tag_ids}
    if not raw:
        return {}
    magnitude = math.sqrt(sum(v * v for v in raw.values()))
    if magnitude <= 0:
        return {}
    return {tid: v / magnitude for tid, v in raw.items()}


def build_tfidf_vectors(
    scene_tags: Mapping[int, Set[int]],
    idf: Mapping[int, float],
) -> Dict[int, Dict[int, float]]:
    """Build L2-normalized TF-IDF vectors for multiple scenes."""
    return {
        scene_id: build_tfidf_vector(tags, idf)
        for scene_id, tags in scene_tags.items()
    }


# ---------------------------------------------------------------------------
# Sparse cosine similarity
# ---------------------------------------------------------------------------

def cosine_similarity(
    vec_a: Mapping[int, float],
    vec_b: Mapping[int, float],
) -> float:
    """Dot-product of two L2-normalized sparse vectors (= cosine similarity)."""
    if not vec_a or not vec_b:
        return 0.0
    # Iterate over the smaller vector for efficiency
    if len(vec_a) > len(vec_b):
        vec_a, vec_b = vec_b, vec_a
    return sum(v * vec_b[k] for k, v in vec_a.items() if k in vec_b)


# ---------------------------------------------------------------------------
# User taste profile
# ---------------------------------------------------------------------------

def build_user_tag_profile(
    *,
    watched_scene_tags: Mapping[int, Set[int]],
    idf: Mapping[int, float],
    engagement_scores: Mapping[int, float] | None = None,
) -> Dict[int, float]:
    """Build an L2-normalized user taste vector from watched scenes.

    Each scene's tag vector is weighted by its engagement score (defaulting to 1.0
    if no scores are available).  The result is a single aggregated preference
    vector suitable for cosine similarity against candidate scene vectors.
    """
    if not watched_scene_tags:
        return {}

    aggregated: Dict[int, float] = defaultdict(float)
    for scene_id, tags in watched_scene_tags.items():
        weight = 1.0
        if engagement_scores:
            weight = max(engagement_scores.get(scene_id, 0.0), 0.0)
            if weight <= 0:
                continue
        for tid in tags:
            idf_val = idf.get(tid, 1.0)
            aggregated[tid] += idf_val * weight

    if not aggregated:
        return {}

    # L2 normalize
    magnitude = math.sqrt(sum(v * v for v in aggregated.values()))
    if magnitude <= 0:
        return {}
    return {tid: v / magnitude for tid, v in aggregated.items()}


def build_user_performer_profile(
    *,
    watched_scene_performers: Mapping[int, Set[int]],
    engagement_scores: Mapping[int, float] | None = None,
) -> Dict[int, float]:
    """Build performer affinity scores from watched scenes.

    Returns ``{performer_id: affinity}`` where affinity is the sum of engagement
    scores for scenes featuring that performer, normalized to [0, 1].
    """
    if not watched_scene_performers:
        return {}

    raw: Dict[int, float] = defaultdict(float)
    for scene_id, performers in watched_scene_performers.items():
        weight = 1.0
        if engagement_scores:
            weight = max(engagement_scores.get(scene_id, 0.0), 0.0)
            if weight <= 0:
                continue
        for pid in performers:
            raw[pid] += weight

    if not raw:
        return {}

    max_val = max(raw.values())
    if max_val <= 0:
        return {}
    return {pid: v / max_val for pid, v in raw.items()}


# ---------------------------------------------------------------------------
# Scene scoring helpers
# ---------------------------------------------------------------------------

def score_scene_against_profile(
    *,
    scene_vector: Mapping[int, float],
    user_profile: Mapping[int, float],
    scene_performers: Set[int] | None = None,
    performer_profile: Mapping[int, float] | None = None,
    scene_studio_id: int | None = None,
    studio_affinity: Mapping[int, float] | None = None,
    tag_weight: float = 0.6,
    performer_weight: float = 0.3,
    studio_weight: float = 0.05,
    embedding_score: float = 0.0,
    embedding_weight: float = 0.0,
    ai_tag_score: float = 0.0,
    ai_tag_weight: float = 0.0,
    negative_penalty: float = 0.0,
    negative_weight: float = 0.0,
) -> Tuple[float, Dict[str, Any]]:
    """Score a single candidate scene against the user profile.

    Returns ``(score, debug_breakdown)`` where score is in [0, 1] and the
    breakdown explains each component for the debug UI.

    When ``negative_weight > 0`` and ``negative_penalty > 0``, the penalty is
    subtracted from the positive score:
    ``final = positive_blend - negative_weight × negative_penalty``.
    """
    # Tag similarity component
    tag_sim = cosine_similarity(scene_vector, user_profile) if scene_vector and user_profile else 0.0

    # Performer overlap component
    perf_score = 0.0
    matched_performers: List[int] = []
    if scene_performers and performer_profile:
        for pid in scene_performers:
            affinity = performer_profile.get(pid, 0.0)
            if affinity > 0:
                perf_score = max(perf_score, affinity)
                matched_performers.append(pid)

    # Studio component
    studio_score = 0.0
    if scene_studio_id is not None and studio_affinity:
        studio_score = studio_affinity.get(scene_studio_id, 0.0)

    # Weighted blend (positive signals)
    total_weight = tag_weight + performer_weight + studio_weight + embedding_weight + ai_tag_weight
    if total_weight <= 0:
        total_weight = 1.0
    positive_score = (
        tag_weight * tag_sim
        + performer_weight * perf_score
        + studio_weight * studio_score
        + embedding_weight * embedding_score
        + ai_tag_weight * ai_tag_score
    ) / total_weight

    # Apply negative penalty
    neg_applied = 0.0
    if negative_weight > 0 and negative_penalty > 0:
        neg_applied = negative_weight * negative_penalty
    score = max(0.0, positive_score - neg_applied)

    debug = {
        "tag_similarity": round(tag_sim, 4),
        "performer_score": round(perf_score, 4),
        "studio_score": round(studio_score, 4),
        "embedding_score": round(embedding_score, 4),
        "ai_tag_score": round(ai_tag_score, 4),
        "negative_penalty": round(neg_applied, 4),
        "matched_performers": sorted(matched_performers),
        "weights": {
            "tag": tag_weight,
            "performer": performer_weight,
            "studio": studio_weight,
            "embedding": embedding_weight,
            "ai_tag": ai_tag_weight,
            "negative": negative_weight,
        },
        "final_score": round(score, 4),
    }
    return score, debug
