"""Taste profile compute service.

Builds and caches user taste profiles, embedding centroids, and content
clusters into the DB tables introduced in migration 0009. Called by the
taste profile API endpoint or a periodic background task.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
from sqlalchemy import delete, select

from stash_ai_server.db.session import get_session_local
from stash_ai_server.models.taste_profile import (
    ContentCluster,
    ContentClusterMember,
    TasteCentroid,
    UserTasteProfile,
)
from stash_ai_server.models.entity_embeddings import EntityEmbedding
from stash_ai_server.recommendations.engagement.scorer import score_all_watched_scenes
from stash_ai_server.recommendations.utils.watch_history import load_watch_history_summary
from stash_ai_server.recommendations.utils.stash_tags import (
    build_user_tag_profile,
    build_user_performer_profile,
    compute_document_frequencies,
    compute_idf,
    fetch_all_scene_tag_ids,
    fetch_scene_tag_ids,
    fetch_tag_names,
    resolve_blacklisted_tag_ids,
)
from stash_ai_server.recommendations.utils.scene_fetch import fetch_scenes_by_ids
from stash_ai_server.recommendations.utils.taste_weighting import (
    build_taste_weights,
    compute_data_depth,
)
from stash_ai_server.recommendations.utils.negative_signals import (
    build_negative_tag_profile,
    build_negative_performer_profile,
    split_by_engagement,
)
from stash_ai_server.recommendations.utils.embedding_similarity import (
    _fetch_embeddings_by_prefix,
    VISUAL_PREFIX,
    VISUAL_DINOV3_PREFIX,
)

_log = logging.getLogger(__name__)

AUDIO_SPEECH_PREFIX = "audio_speech"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _extract_performers(payloads: Dict[int, Dict[str, Any]]) -> Tuple[Dict[int, Set[int]], Dict[int, str]]:
    """Return ({scene_id: {performer_id}}, {performer_id: name})."""
    scene_perfs: Dict[int, Set[int]] = {}
    names: Dict[int, str] = {}
    for scene_id, payload in payloads.items():
        pids: Set[int] = set()
        for p in payload.get("performers", []):
            pid = p.get("id")
            name = p.get("name")
            if pid is not None:
                try:
                    pid = int(pid)
                    pids.add(pid)
                    if name:
                        names[pid] = str(name)
                except (TypeError, ValueError):
                    continue
        if pids:
            scene_perfs[scene_id] = pids
    return scene_perfs, names


def _compute_centroid(vectors: List[List[float]]) -> Optional[List[float]]:
    """Compute L2-normalized mean of vectors."""
    if not vectors:
        return None
    arr = np.array(vectors, dtype=np.float32)
    mean = arr.mean(axis=0)
    norm = np.linalg.norm(mean)
    if norm < 1e-9:
        return None
    return (mean / norm).tolist()


def _get_rated_only_scene_ids(watched_set: Set[int]) -> Set[int]:
    """Return scene IDs that have explicit ratings but NO watch history."""
    import sqlalchemy as sa
    rated: Set[int] = set()

    # AI custom ratings
    try:
        from stash_ai_server.models.ratings import EntityRating
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

    # Stash native ratings
    try:
        from stash_ai_server.utils.stash_db import get_stash_session_factory
        factory = get_stash_session_factory()
        if factory:
            with factory() as session:
                scenes_table = sa.Table(
                    "scenes", sa.MetaData(), autoload_with=session.bind,
                )
                rating_col = None
                for col in scenes_table.columns:
                    if col.name == "rating":
                        rating_col = col
                        break
                if rating_col is not None:
                    rows = session.execute(
                        sa.select(scenes_table.c.id).where(
                            rating_col.isnot(None), rating_col != 0,
                        )
                    ).scalars().all()
                    rated.update(int(r) for r in rows)
    except Exception:
        _log.debug("_get_rated_only_scene_ids: Stash DB unavailable", exc_info=True)

    return rated - watched_set


def _get_rating_scores_for_scenes(scene_ids: Set[int]) -> Dict[int, float]:
    """Return engagement-like scores (0.0-1.0) derived from explicit ratings."""
    import sqlalchemy as sa
    scores: Dict[int, float] = {}

    # AI custom ratings (0-100 scale)
    try:
        from stash_ai_server.models.ratings import EntityRating
        with get_session_local()() as session:
            rows = session.execute(
                sa.select(EntityRating.entity_id, EntityRating.value).where(
                    EntityRating.entity_type == "scene",
                    EntityRating.rating_key == "default",
                    EntityRating.entity_id.in_([str(s) for s in scene_ids]),
                )
            ).all()
            for eid, val in rows:
                try:
                    sid = int(eid)
                    scores[sid] = max(0.0, min(1.0, float(val) / 100.0))
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass

    # Stash native ratings (fill in for scenes not covered by AI rating)
    try:
        from stash_ai_server.utils.stash_db import get_stash_session_factory
        factory = get_stash_session_factory()
        if factory:
            with factory() as session:
                scenes_table = sa.Table(
                    "scenes", sa.MetaData(), autoload_with=session.bind,
                )
                rating_col = None
                for col in scenes_table.columns:
                    if col.name == "rating":
                        rating_col = col
                        break
                if rating_col is not None:
                    missing = [s for s in scene_ids if s not in scores]
                    if missing:
                        rows = session.execute(
                            sa.select(scenes_table.c.id, rating_col).where(
                                scenes_table.c.id.in_(missing),
                                rating_col.isnot(None), rating_col != 0,
                            )
                        ).all()
                        for sid, val in rows:
                            try:
                                scores[int(sid)] = max(0.0, min(1.0, float(val) / 100.0))
                            except (TypeError, ValueError):
                                pass
    except Exception:
        _log.debug("_get_rating_scores_for_scenes: Stash DB unavailable", exc_info=True)

    return scores


# ---------------------------------------------------------------------------
# Profile builder
# ---------------------------------------------------------------------------

def compute_and_store_profile(
    *,
    profile_type: str = "global",
    history_limit: int = 400,
    min_watch_seconds: float = 15.0,
    recency_half_life: float = 30.0,
    top_tags: int = 60,
    top_performers: int = 40,
) -> Dict[str, Any]:
    """Compute user taste profile and store it in the DB.

    Returns the profile dict for immediate use.
    """
    t0 = time.perf_counter()

    # Watch history
    history = load_watch_history_summary(
        min_watch_seconds=min_watch_seconds, limit=history_limit,
    )

    watched_ids = [e["scene_id"] for e in history] if history else []
    watched_set = set(watched_ids)

    # Include rated-but-unwatched scenes in the reference set
    rated_only_ids = _get_rated_only_scene_ids(watched_set)
    if rated_only_ids:
        _log.info(
            "compute_profile: adding %d rated-but-unwatched scenes to reference set",
            len(rated_only_ids),
        )
        watched_ids = watched_ids + list(rated_only_ids)
        watched_set = watched_set | rated_only_ids

    if not watched_ids:
        return {"watched_scenes": 0}

    # Engagement (include_rated=True brings in rated-but-unwatched)
    engagement_map: Dict[int, float] = {}
    try:
        results = score_all_watched_scenes(limit=history_limit, include_rated=True)
        engagement_map = {r.entity_id: r.score for r in results}
    except Exception:
        _log.debug("compute_profile: engagement scoring unavailable")

    # For rated-but-unwatched scenes with no engagement score, synthesize
    # a score from their explicit rating (0-100 scale → 0.0-1.0)
    if rated_only_ids:
        rating_scores = _get_rating_scores_for_scenes(rated_only_ids)
        for sid in rated_only_ids:
            if sid not in engagement_map:
                engagement_map[sid] = rating_scores.get(sid, 0.5)

    # Corpus / IDF
    blacklisted = resolve_blacklisted_tag_ids()
    corpus = fetch_all_scene_tag_ids(exclude_tag_ids=blacklisted)
    df_map, total_docs = compute_document_frequencies(corpus)
    idf = compute_idf(df_map, total_docs)

    # Tags, payloads, performers
    watched_tags = fetch_scene_tag_ids(watched_ids, exclude_tag_ids=blacklisted)
    watched_payloads = fetch_scenes_by_ids(watched_ids)
    watched_performers, performer_names = _extract_performers(watched_payloads)

    # Taste weights
    if engagement_map and recency_half_life > 0:
        depth_scores = compute_data_depth(
            watched_ids, corpus_tags=corpus, performer_map=watched_performers,
        )
        taste_weights = build_taste_weights(
            engagement_map, history,
            half_life_days=recency_half_life, data_depth=depth_scores,
        )
    else:
        taste_weights = engagement_map

    # Tag profile
    user_profile = build_user_tag_profile(
        watched_scene_tags=watched_tags, idf=idf,
        engagement_scores=taste_weights if taste_weights else None,
    )

    # Performer profile
    perf_profile = build_user_performer_profile(
        watched_scene_performers=watched_performers,
        engagement_scores=taste_weights if taste_weights else None,
    )

    # Negative profiles
    neg_tags_profile: Dict[int, float] = {}
    neg_perf_profile: Dict[int, float] = {}
    liked_ids: List[int] = []
    disliked_ids: List[int] = []
    if engagement_map:
        liked_ids, disliked_ids = split_by_engagement(watched_ids, engagement_map)
        if disliked_ids:
            disliked_tags = {s: watched_tags.get(s, set()) for s in disliked_ids if s in watched_tags}
            liked_tags = {s: watched_tags.get(s, set()) for s in liked_ids if s in watched_tags}
            neg_tags_profile = build_negative_tag_profile(
                disliked_scene_tags=disliked_tags, liked_scene_tags=liked_tags, idf=idf,
            )
            disliked_perfs = {s: watched_performers.get(s, set()) for s in disliked_ids if s in watched_performers}
            liked_perfs = {s: watched_performers.get(s, set()) for s in liked_ids if s in watched_performers}
            neg_perf_profile = build_negative_performer_profile(
                disliked_scene_performers=disliked_perfs, liked_scene_performers=liked_perfs,
            )

    # Embedding coverage
    embed_grouped = _fetch_embeddings_by_prefix(watched_ids, VISUAL_PREFIX)
    scenes_with_visual: Set[int] = set()
    for entries in embed_grouped.values():
        for eid, _ in entries:
            scenes_with_visual.add(eid)

    # Format affinities
    all_tag_ids = set(user_profile.keys()) | set(neg_tags_profile.keys())
    tag_names = fetch_tag_names(all_tag_ids)

    max_pos = max(user_profile.values()) if user_profile else 1.0
    tag_affinities = []
    for tid, weight in sorted(user_profile.items(), key=lambda x: x[1], reverse=True)[:top_tags]:
        neg_w = neg_tags_profile.get(tid, 0.0)
        pos_score = (weight / max_pos) * 50.0 if max_pos > 0 else 0.0
        tag_affinities.append({
            "tag_id": tid,
            "tag_name": tag_names.get(tid, f"tag_{tid}"),
            "affinity": round(min(100.0, 50.0 + pos_score), 1),
            "positive_weight": round(weight, 6),
            "negative_weight": round(neg_w, 6),
            "doc_frequency": df_map.get(tid, 0),
        })

    top_pos_ids = {t["tag_id"] for t in tag_affinities}
    neg_tag_list = []
    max_neg = max(neg_tags_profile.values()) if neg_tags_profile else 1.0
    for tid, weight in sorted(neg_tags_profile.items(), key=lambda x: x[1], reverse=True):
        if tid in top_pos_ids:
            continue
        neg_score = (weight / max_neg) * 50.0 if max_neg > 0 else 0.0
        neg_tag_list.append({
            "tag_id": tid,
            "tag_name": tag_names.get(tid, f"tag_{tid}"),
            "affinity": round(max(0.0, 50.0 - neg_score), 1),
            "negative_weight": round(weight, 6),
            "doc_frequency": df_map.get(tid, 0),
        })

    perf_affinities = []
    for pid, val in sorted(perf_profile.items(), key=lambda x: x[1], reverse=True)[:top_performers]:
        neg_p = neg_perf_profile.get(pid, 0.0)
        perf_affinities.append({
            "performer_id": pid,
            "performer_name": performer_names.get(pid, f"performer_{pid}"),
            "affinity": round(50.0 + val * 50.0, 1),
            "positive_weight": round(val, 4),
            "negative_weight": round(neg_p, 4),
        })

    neg_perf_list = []
    top_pos_perfs = {p["performer_id"] for p in perf_affinities}
    for pid, weight in sorted(neg_perf_profile.items(), key=lambda x: x[1], reverse=True):
        if pid in top_pos_perfs:
            continue
        neg_perf_list.append({
            "performer_id": pid,
            "performer_name": performer_names.get(pid, f"performer_{pid}"),
            "affinity": round(max(0.0, 50.0 - weight * 50.0), 1),
            "negative_weight": round(weight, 4),
        })

    # Engagement stats
    eng_values = sorted(engagement_map.values()) if engagement_map else []
    eng_stats = {}
    if eng_values:
        import statistics
        eng_stats = {
            "min": round(eng_values[0], 4),
            "max": round(eng_values[-1], 4),
            "mean": round(statistics.mean(eng_values), 4),
            "median": round(statistics.median(eng_values), 4),
            "p25": round(eng_values[max(0, len(eng_values) * 25 // 100 - 1)], 4),
            "p75": round(eng_values[max(0, len(eng_values) * 75 // 100 - 1)], 4),
        }

    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    profile_data = {
        "watched_scenes": len(watched_set),
        "liked_scenes": len(liked_ids),
        "disliked_scenes": len(disliked_ids),
        "corpus_size": total_docs,
        "engagement_stats": eng_stats,
        "tags": tag_affinities,
        "negative_tags": neg_tag_list[:top_tags],
        "performers": perf_affinities,
        "negative_performers": neg_perf_list[:top_performers],
        "embedding_stats": {
            "scenes_with_visual": len(scenes_with_visual),
            "total_watched": len(watched_set),
            "coverage_pct": round(len(scenes_with_visual) / max(len(watched_set), 1) * 100, 1),
        },
    }

    # Persist to DB
    try:
        with get_session_local()() as session:
            # Upsert profile
            existing = session.execute(
                select(UserTasteProfile).where(UserTasteProfile.profile_type == profile_type)
            ).scalar_one_or_none()

            if existing:
                existing.watched_count = len(watched_set)
                existing.liked_count = len(liked_ids)
                existing.disliked_count = len(disliked_ids)
                existing.tag_affinities = tag_affinities
                existing.negative_tags = neg_tag_list[:top_tags]
                existing.performer_affinities = perf_affinities
                existing.negative_performers = neg_perf_list[:top_performers]
                existing.engagement_stats = eng_stats
                existing.embedding_coverage_pct = profile_data["embedding_stats"]["coverage_pct"]
                existing.computation_ms = elapsed_ms
                existing.config_json = {
                    "history_limit": history_limit,
                    "min_watch_seconds": min_watch_seconds,
                    "recency_half_life": recency_half_life,
                    "top_tags": top_tags,
                    "top_performers": top_performers,
                }
                import datetime as dt
                existing.computed_at = dt.datetime.now(dt.timezone.utc)
            else:
                import datetime as dt
                row = UserTasteProfile(
                    profile_type=profile_type,
                    watched_count=len(watched_set),
                    liked_count=len(liked_ids),
                    disliked_count=len(disliked_ids),
                    tag_affinities=tag_affinities,
                    negative_tags=neg_tag_list[:top_tags],
                    performer_affinities=perf_affinities,
                    negative_performers=neg_perf_list[:top_performers],
                    engagement_stats=eng_stats,
                    embedding_coverage_pct=profile_data["embedding_stats"]["coverage_pct"],
                    computation_ms=elapsed_ms,
                    computed_at=dt.datetime.now(dt.timezone.utc),
                    config_json={
                        "history_limit": history_limit,
                        "min_watch_seconds": min_watch_seconds,
                        "recency_half_life": recency_half_life,
                        "top_tags": top_tags,
                        "top_performers": top_performers,
                    },
                )
                session.add(row)
            session.commit()
        _log.info("Stored taste profile '%s' (%d ms)", profile_type, elapsed_ms)
    except Exception:
        _log.exception("Failed to persist taste profile")

    profile_data["computation_ms"] = elapsed_ms
    return profile_data


# ---------------------------------------------------------------------------
# Centroid builder
# ---------------------------------------------------------------------------

def compute_and_store_centroids(
    *,
    history_limit: int = 400,
    min_watch_seconds: float = 15.0,
    max_liked_centroids: int = 5,
) -> Dict[str, Any]:
    """Compute liked/disliked embedding centroids and persist them.

    For *liked* scenes, computes **multiple** centroids via k-means when
    the user has diverse taste, keeping each taste niche sharp instead of
    averaging everything into a single muddy centroid.  For *disliked*
    scenes, a single centroid suffices (we just need a rough direction to
    penalise).
    """
    history = load_watch_history_summary(
        min_watch_seconds=min_watch_seconds, limit=history_limit,
    )
    if not history:
        return {"status": "no_history"}

    watched_ids = [e["scene_id"] for e in history]

    engagement_map: Dict[int, float] = {}
    try:
        results = score_all_watched_scenes(limit=history_limit)
        engagement_map = {r.entity_id: r.score for r in results}
    except Exception:
        _log.debug("centroids: engagement scoring unavailable")

    if not engagement_map:
        return {"status": "no_engagement"}

    liked_ids, disliked_ids = split_by_engagement(watched_ids, engagement_map)

    stored = []

    for prefix_name, prefix in [("visual_metaclip2", VISUAL_PREFIX), ("visual_dinov3", VISUAL_DINOV3_PREFIX), ("audio_speech", AUDIO_SPEECH_PREFIX)]:
        # ── Liked: multi-centroid via k-means ──
        if liked_ids:
            embed_grouped = _fetch_embeddings_by_prefix(liked_ids, prefix)
            all_vecs: List[List[float]] = []
            scenes_with_embeds: Set[int] = set()
            for entries in embed_grouped.values():
                for _eid, vec in entries:
                    if vec:
                        all_vecs.append(vec)
                        scenes_with_embeds.add(_eid)

            if all_vecs:
                n_scenes = len(scenes_with_embeds)
                # Choose K: 1 for few scenes, scale up for more
                if n_scenes < 6:
                    k = 1
                else:
                    k = min(max(2, n_scenes // 8), max_liked_centroids)

                arr = np.array(all_vecs, dtype=np.float32)
                dim = arr.shape[1]

                if k == 1:
                    # Single centroid — simple mean
                    centroid_vecs = [_compute_centroid(all_vecs)]
                    cluster_scene_counts = [n_scenes]
                else:
                    # K-means on the liked embeddings to find taste sub-clusters
                    # Each vector may be a different section of the same scene;
                    # k-means on all vectors captures intra-scene diversity too
                    actual_k = min(k, len(arr))
                    labels, centroids_raw = _kmeans(arr, actual_k, max_iter=30)

                    centroid_vecs = []
                    cluster_scene_counts = []
                    for ci in range(actual_k):
                        mask = labels == ci
                        if not mask.any():
                            continue
                        c_vec = centroids_raw[ci]
                        norm = np.linalg.norm(c_vec)
                        if norm < 1e-9:
                            continue
                        centroid_vecs.append((c_vec / norm).tolist())
                        # Count unique scenes in this sub-cluster
                        c_scene_ids = set()
                        for idx in np.where(mask)[0]:
                            # Map vector index back to scene: vectors are in order
                            # of embed_grouped iteration which preserves entity_id
                            pass
                        cluster_scene_counts.append(int(mask.sum()))

                    if not centroid_vecs:
                        centroid_vecs = [_compute_centroid(all_vecs)]
                        cluster_scene_counts = [n_scenes]

                _log.info(
                    "centroids: liked/%s — %d scenes, %d vectors → %d centroids (k=%d)",
                    prefix_name, n_scenes, len(all_vecs), len(centroid_vecs), k,
                )

                # Delete old liked_* centroids for this prefix, then store new ones
                try:
                    with get_session_local()() as session:
                        # Remove old liked centroids (both "liked" and "liked_N" patterns)
                        old = session.execute(
                            select(TasteCentroid).where(
                                TasteCentroid.centroid_type.like("liked%"),
                                TasteCentroid.embedding_type == prefix_name,
                            )
                        ).scalars().all()
                        for r in old:
                            session.delete(r)
                        session.flush()

                        import datetime as dt_mod
                        for ci, c_vec in enumerate(centroid_vecs):
                            if c_vec is None:
                                continue
                            ctype = f"liked_{ci}" if len(centroid_vecs) > 1 else "liked_0"
                            row = TasteCentroid(
                                centroid_type=ctype,
                                embedding_type=prefix_name,
                                centroid=c_vec,
                                dim=len(c_vec),
                                scene_count=cluster_scene_counts[ci] if ci < len(cluster_scene_counts) else n_scenes,
                                computed_at=dt_mod.datetime.now(dt_mod.timezone.utc),
                            )
                            session.add(row)
                        session.commit()
                    stored.append(f"liked_{prefix_name} ({len(centroid_vecs)} centroids)")
                except Exception:
                    _log.exception("Failed to persist liked centroids %s", prefix_name)

        # ── Disliked: single centroid ──
        if disliked_ids:
            embed_grouped = _fetch_embeddings_by_prefix(disliked_ids, prefix)
            all_vecs_d: List[List[float]] = []
            scenes_d: Set[int] = set()
            for entries in embed_grouped.values():
                for _eid, vec in entries:
                    if vec:
                        all_vecs_d.append(vec)
                        scenes_d.add(_eid)

            centroid_vec = _compute_centroid(all_vecs_d)
            if centroid_vec is not None:
                try:
                    with get_session_local()() as session:
                        existing = session.execute(
                            select(TasteCentroid).where(
                                TasteCentroid.centroid_type == "disliked",
                                TasteCentroid.embedding_type == prefix_name,
                            )
                        ).scalar_one_or_none()

                        import datetime as dt_mod
                        if existing:
                            existing.centroid = centroid_vec
                            existing.dim = len(centroid_vec)
                            existing.scene_count = len(scenes_d)
                            existing.computed_at = dt_mod.datetime.now(dt_mod.timezone.utc)
                        else:
                            row = TasteCentroid(
                                centroid_type="disliked",
                                embedding_type=prefix_name,
                                centroid=centroid_vec,
                                dim=len(centroid_vec),
                                scene_count=len(scenes_d),
                                computed_at=dt_mod.datetime.now(dt_mod.timezone.utc),
                            )
                            session.add(row)
                        session.commit()
                    stored.append(f"disliked_{prefix_name}")
                except Exception:
                    _log.exception("Failed to persist disliked centroid %s", prefix_name)

    return {"stored": stored, "liked_count": len(liked_ids), "disliked_count": len(disliked_ids)}


# ---------------------------------------------------------------------------
# Content cluster builder
# ---------------------------------------------------------------------------

def compute_and_store_clusters(
    *,
    n_clusters: int = 20,
    embedding_prefix: str = VISUAL_PREFIX,
    min_scenes_per_cluster: int = 3,
) -> Dict[str, Any]:
    """Cluster all scenes with embeddings into content buckets.

    Uses k-means on visual embeddings.  Stores clusters with centroid,
    top tags, and scene membership.
    """
    # Fetch all scene visual embeddings (use section 0 as representative)
    with get_session_local()() as session:
        rows = session.execute(
            select(
                EntityEmbedding.entity_id,
                EntityEmbedding.embedding,
            ).where(
                EntityEmbedding.embedding_type == f"{embedding_prefix}_section_0",
                EntityEmbedding.entity_type == "scene",
            )
        ).all()

    if len(rows) < n_clusters * min_scenes_per_cluster:
        _log.info("clusters: only %d scenes with embeddings (need %d), skipping", len(rows), n_clusters * min_scenes_per_cluster)
        return {"status": "insufficient_data", "scene_count": len(rows)}

    scene_ids = [r.entity_id for r in rows]
    vectors = np.array([list(r.embedding) for r in rows], dtype=np.float32)

    # Simple k-means (numpy-only, no sklearn dependency)
    labels, centroids = _kmeans(vectors, n_clusters, max_iter=50)

    # Fetch tags for label generation
    blacklisted = resolve_blacklisted_tag_ids()
    scene_tags = fetch_scene_tag_ids(scene_ids, exclude_tag_ids=blacklisted)
    all_tag_ids: Set[int] = set()
    for tags in scene_tags.values():
        all_tag_ids |= tags
    tag_names = fetch_tag_names(all_tag_ids) if all_tag_ids else {}

    # Engagement
    engagement_map: Dict[int, float] = {}
    try:
        results = score_all_watched_scenes(limit=2000)
        engagement_map = {r.entity_id: r.score for r in results}
    except Exception:
        pass

    # Build cluster metadata
    cluster_data = defaultdict(lambda: {"scene_ids": [], "distances": [], "tags": defaultdict(int)})
    for i, sid in enumerate(scene_ids):
        c = int(labels[i])
        dist = float(np.linalg.norm(vectors[i] - centroids[c]))
        cluster_data[c]["scene_ids"].append(sid)
        cluster_data[c]["distances"].append(dist)
        for tid in scene_tags.get(sid, set()):
            cluster_data[c]["tags"][tid] += 1

    # Persist
    try:
        with get_session_local()() as session:
            # Clear old clusters
            session.execute(delete(ContentClusterMember))
            session.execute(delete(ContentCluster))
            session.flush()

            for c_idx, cdata in cluster_data.items():
                sids = cdata["scene_ids"]
                if len(sids) < min_scenes_per_cluster:
                    continue

                # Top tags for label
                sorted_tags = sorted(cdata["tags"].items(), key=lambda x: x[1], reverse=True)[:10]
                top_tag_list = [
                    {"tag_id": tid, "tag_name": tag_names.get(tid, f"tag_{tid}"), "weight": cnt}
                    for tid, cnt in sorted_tags
                ]

                # Label from top 3 tags
                label_parts = [t["tag_name"] for t in top_tag_list[:3]]
                cluster_label = " / ".join(label_parts) if label_parts else f"Cluster {c_idx}"

                # Average engagement
                eng_vals = [engagement_map.get(sid, 0) for sid in sids if sid in engagement_map]
                avg_eng = sum(eng_vals) / len(eng_vals) if eng_vals else None

                centroid_vec = centroids[c_idx].tolist()
                dim = len(centroid_vec)

                cluster = ContentCluster(
                    cluster_label=cluster_label,
                    top_tags=top_tag_list,
                    centroid=centroid_vec,
                    dim=dim,
                    scene_count=len(sids),
                    avg_engagement=avg_eng,
                )
                session.add(cluster)
                session.flush()  # get cluster.id

                for sid, dist in zip(sids, cdata["distances"]):
                    member = ContentClusterMember(
                        cluster_id=cluster.id,
                        scene_id=sid,
                        distance=dist,
                    )
                    session.add(member)

            session.commit()
            _log.info("Stored %d content clusters", len(cluster_data))
    except Exception:
        _log.exception("Failed to persist content clusters")
        return {"status": "error"}

    return {
        "status": "ok",
        "clusters": len(cluster_data),
        "total_scenes": len(scene_ids),
    }


def _kmeans(X: np.ndarray, k: int, max_iter: int = 50) -> Tuple[np.ndarray, np.ndarray]:
    """Simple k-means. Returns (labels, centroids)."""
    n = X.shape[0]
    rng = np.random.default_rng(42)
    indices = rng.choice(n, size=k, replace=False)
    centroids = X[indices].copy()

    labels = np.zeros(n, dtype=np.int32)
    for _ in range(max_iter):
        # Assign
        dists = np.linalg.norm(X[:, None, :] - centroids[None, :, :], axis=2)
        new_labels = dists.argmin(axis=1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        # Update centroids
        for c in range(k):
            mask = labels == c
            if mask.any():
                centroids[c] = X[mask].mean(axis=0)

    return labels, centroids


# ---------------------------------------------------------------------------
# Cached profile retrieval
# ---------------------------------------------------------------------------

def get_cached_profile(profile_type: str = "global") -> Optional[Dict[str, Any]]:
    """Return the cached taste profile from DB, or None if not computed."""
    try:
        with get_session_local()() as session:
            row = session.execute(
                select(UserTasteProfile).where(UserTasteProfile.profile_type == profile_type)
            ).scalar_one_or_none()
            if row is None:
                return None
            return {
                "watched_scenes": row.watched_count,
                "liked_scenes": row.liked_count,
                "disliked_scenes": row.disliked_count,
                "tags": row.tag_affinities,
                "negative_tags": row.negative_tags,
                "performers": row.performer_affinities,
                "negative_performers": row.negative_performers,
                "engagement_stats": row.engagement_stats,
                "embedding_stats": {
                    "coverage_pct": row.embedding_coverage_pct,
                },
                "computed_at": row.computed_at.isoformat() if row.computed_at else None,
                "computation_ms": row.computation_ms,
            }
    except Exception:
        _log.exception("Failed to load cached profile")
        return None


def get_cached_centroids() -> List[Dict[str, Any]]:
    """Return all cached taste centroids."""
    try:
        with get_session_local()() as session:
            rows = session.execute(select(TasteCentroid)).scalars().all()
            return [
                {
                    "centroid_type": r.centroid_type,
                    "embedding_type": r.embedding_type,
                    "dim": r.dim,
                    "scene_count": r.scene_count,
                    "computed_at": r.computed_at.isoformat() if r.computed_at else None,
                }
                for r in rows
            ]
    except Exception:
        _log.exception("Failed to load cached centroids")
        return []


def get_cached_clusters() -> List[Dict[str, Any]]:
    """Return all cached content clusters (without membership details)."""
    try:
        with get_session_local()() as session:
            rows = session.execute(
                select(ContentCluster).order_by(ContentCluster.scene_count.desc())
            ).scalars().all()
            return [
                {
                    "id": r.id,
                    "label": r.cluster_label,
                    "top_tags": r.top_tags,
                    "scene_count": r.scene_count,
                    "avg_engagement": r.avg_engagement,
                    "user_affinity": r.user_affinity,
                    "computed_at": r.computed_at.isoformat() if r.computed_at else None,
                }
                for r in rows
            ]
    except Exception:
        _log.exception("Failed to load cached clusters")
        return []


def get_cluster_scene_ids(cluster_id: int, *, limit: int = 50) -> List[int]:
    """Return scene IDs belonging to a content cluster, ordered by distance."""
    try:
        with get_session_local()() as session:
            rows = session.execute(
                select(ContentClusterMember.scene_id)
                .where(ContentClusterMember.cluster_id == cluster_id)
                .order_by(ContentClusterMember.distance.asc())
                .limit(limit)
            ).scalars().all()
        return [int(r) for r in rows]
    except Exception:
        _log.exception("Failed to load cluster scene IDs for cluster %d", cluster_id)
        return []


def get_centroid_scene_ids(
    *,
    centroid_type: str = "liked_0",
    embedding_type: str = "visual_metaclip2",
    limit: int = 30,
) -> List[int]:
    """Return scene IDs closest to a specific taste centroid via pgvector."""
    try:
        with get_session_local()() as session:
            row = session.execute(
                select(TasteCentroid).where(
                    TasteCentroid.centroid_type == centroid_type,
                    TasteCentroid.embedding_type == embedding_type,
                )
            ).scalar_one_or_none()

        if not row or row.centroid is None:
            return []

        from stash_ai_server.db.embedding_store import find_similar_entities
        centroid_vec = list(row.centroid)
        emb_type = f"{embedding_type}_section_0"
        results = find_similar_entities(
            embedding_type=emb_type,
            query_vector=centroid_vec,
            limit=limit,
            entity_type="scene",
        )
        return [r.entity_id for r in results]
    except Exception:
        _log.exception("Failed to find scenes near centroid %s/%s", centroid_type, embedding_type)
        return []
