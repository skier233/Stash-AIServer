"""Store and query functions for detection tracks, face embeddings, and clusters.

This module provides the data-access layer for the detection/face-recognition
tables.  It follows the same patterns as ``ai_results_store.py`` — synchronous
core with ``_async`` wrappers via ``asyncio.to_thread``.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any, Sequence

import numpy as np
import sqlalchemy as sa
from sqlalchemy import select, update, delete, func
from sqlalchemy.orm import Session

from stash_ai_server.db.session import get_session_local
from stash_ai_server.models.detections import (
    DetectionTrack,
    FaceCluster,
    FaceEmbedding,
)

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Detection Tracks
# ---------------------------------------------------------------------------

def store_detection_track(
    session: Session,
    *,
    run_id: int,
    entity_type: str,
    entity_id: int,
    label: str,
    bbox: list[float],
    score: float,
    detector: str,
    start_s: float | None = None,
    end_s: float | None = None,
    keyframes: list[dict] | None = None,
    metadata: dict | None = None,
) -> DetectionTrack:
    """Insert a single detection track and return the ORM object (flushed, not committed)."""
    track = DetectionTrack(
        run_id=run_id,
        entity_type=entity_type,
        entity_id=entity_id,
        label=label,
        bbox=bbox,
        score=score,
        detector=detector,
        start_s=start_s,
        end_s=end_s,
        keyframes=keyframes,
        metadata_=metadata,
    )
    session.add(track)
    session.flush()
    return track


def cleanup_stale_detections(
    *,
    entity_type: str,
    entity_id: int,
    service: str,
    exclude_run_id: int,
) -> int:
    """Delete detection tracks from prior runs for this entity.

    Based on the cascade, this also removes associated ``face_embeddings``
    rows.  Returns the number of tracks deleted.
    """
    with get_session_local()() as session:
        # Subquery: run_ids for this entity from the same service, excluding current
        from stash_ai_server.models.ai_results import AIModelRun

        stale_run_ids = (
            select(AIModelRun.id)
            .where(
                AIModelRun.service == service,
                AIModelRun.entity_type == entity_type,
                AIModelRun.entity_id == entity_id,
                AIModelRun.id != exclude_run_id,
            )
        )
        result = session.execute(
            delete(DetectionTrack).where(
                DetectionTrack.run_id.in_(stale_run_ids),
            )
        )
        deleted = result.rowcount  # type: ignore[union-attr]
        session.commit()
        return deleted


async def cleanup_stale_detections_async(**kwargs: Any) -> int:
    return await asyncio.to_thread(cleanup_stale_detections, **kwargs)


# ---------------------------------------------------------------------------
# Face Clusters
# ---------------------------------------------------------------------------

def find_nearest_cluster(
    embedding: list[float] | np.ndarray,
    *,
    exclude_statuses: Sequence[str] = ("ignored", "merged_away"),
    limit: int = 1,
) -> list[tuple[int, float]]:
    """ANN search on ``face_clusters.centroid``.

    Returns ``[(cluster_id, cosine_similarity)]`` ordered by similarity desc.
    """
    vec = list(embedding) if isinstance(embedding, np.ndarray) else embedding

    with get_session_local()() as session:
        # pgvector <=> operator returns cosine *distance* (1 - similarity)
        distance_expr = FaceCluster.centroid.cosine_distance(vec)
        similarity_expr = (1 - distance_expr).label("similarity")

        stmt = (
            select(FaceCluster.id, similarity_expr)
            .where(
                FaceCluster.status.notin_(list(exclude_statuses)),
                FaceCluster.centroid.isnot(None),
            )
            .order_by(distance_expr)
            .limit(limit)
        )
        rows = session.execute(stmt).all()
        return [(row[0], float(row[1])) for row in rows]


async def find_nearest_cluster_async(
    embedding: list[float] | np.ndarray, **kwargs: Any,
) -> list[tuple[int, float]]:
    return await asyncio.to_thread(find_nearest_cluster, embedding, **kwargs)


def create_cluster(
    session: Session,
    *,
    status: str = "unidentified",
    performer_id: int | None = None,
    label: str | None = None,
) -> FaceCluster:
    """Create a new face cluster. Returns the flushed ORM object."""
    cluster = FaceCluster(
        status=status,
        performer_id=performer_id,
        label=label,
    )
    session.add(cluster)
    session.flush()
    return cluster


def update_cluster_centroid(cluster_id: int) -> None:
    """Recompute the centroid of a cluster as the mean of its exemplar embeddings."""
    with get_session_local()() as session:
        # Fetch exemplar vectors
        stmt = (
            select(FaceEmbedding.embedding)
            .where(
                FaceEmbedding.cluster_id == cluster_id,
                FaceEmbedding.is_exemplar.is_(True),
            )
        )
        rows = session.execute(stmt).all()
        if not rows:
            _log.debug("Cluster %d has no exemplars; clearing centroid", cluster_id)
            session.execute(
                update(FaceCluster)
                .where(FaceCluster.id == cluster_id)
                .values(centroid=None, sample_count=0, updated_at=dt.datetime.now(dt.timezone.utc))
            )
            session.commit()
            return

        vectors = [np.array(row[0], dtype=np.float32) for row in rows]
        mean_vec = np.mean(vectors, axis=0)
        # L2-normalise the centroid
        norm = np.linalg.norm(mean_vec)
        if norm > 0:
            mean_vec = mean_vec / norm

        # Best quality score among all embeddings in this cluster
        best_score_row = session.execute(
            select(func.max(FaceEmbedding.score))
            .where(FaceEmbedding.cluster_id == cluster_id)
        ).scalar()

        session.execute(
            update(FaceCluster)
            .where(FaceCluster.id == cluster_id)
            .values(
                centroid=mean_vec.tolist(),
                sample_count=len(vectors),
                quality_score=best_score_row,
                updated_at=dt.datetime.now(dt.timezone.utc),
            )
        )
        session.commit()


async def update_cluster_centroid_async(cluster_id: int) -> None:
    await asyncio.to_thread(update_cluster_centroid, cluster_id)


def link_performer(cluster_id: int, performer_id: int) -> None:
    """Set performer_id and status='identified' on a cluster."""
    with get_session_local()() as session:
        session.execute(
            update(FaceCluster)
            .where(FaceCluster.id == cluster_id)
            .values(
                performer_id=performer_id,
                status="identified",
                updated_at=dt.datetime.now(dt.timezone.utc),
            )
        )
        session.commit()


async def link_performer_async(cluster_id: int, performer_id: int) -> None:
    await asyncio.to_thread(link_performer, cluster_id, performer_id)


def ignore_cluster(cluster_id: int) -> None:
    """Set status='ignored' on a cluster."""
    with get_session_local()() as session:
        session.execute(
            update(FaceCluster)
            .where(FaceCluster.id == cluster_id)
            .values(status="ignored", updated_at=dt.datetime.now(dt.timezone.utc))
        )
        session.commit()


async def ignore_cluster_async(cluster_id: int) -> None:
    await asyncio.to_thread(ignore_cluster, cluster_id)


def merge_clusters(surviving_id: int, absorbed_id: int) -> None:
    """Merge one cluster into another.

    Re-parents all embeddings to the surviving cluster, marks the absorbed
    cluster as ``merged_away``, and recomputes the surviving cluster's centroid.
    """
    with get_session_local()() as session:
        # Re-parent embeddings
        session.execute(
            update(FaceEmbedding)
            .where(FaceEmbedding.cluster_id == absorbed_id)
            .values(cluster_id=surviving_id)
        )
        # Mark absorbed cluster
        session.execute(
            update(FaceCluster)
            .where(FaceCluster.id == absorbed_id)
            .values(
                status="merged_away",
                merged_into_id=surviving_id,
                updated_at=dt.datetime.now(dt.timezone.utc),
            )
        )
        session.commit()

    # Recompute surviving centroid (and exemplar set)
    _recompute_exemplars(surviving_id)
    update_cluster_centroid(surviving_id)


async def merge_clusters_async(surviving_id: int, absorbed_id: int) -> None:
    await asyncio.to_thread(merge_clusters, surviving_id, absorbed_id)


# ---------------------------------------------------------------------------
# Face Embeddings
# ---------------------------------------------------------------------------

def store_face_embedding(
    session: Session,
    *,
    track_id: int,
    cluster_id: int | None,
    entity_type: str,
    entity_id: int,
    embedding: list[float],
    norm: float,
    score: float,
    bbox: list[float] | None = None,
    timestamp_s: float | None = None,
    is_exemplar: bool = False,
    embedder: str,
) -> FaceEmbedding:
    """Insert a single face embedding. Returns the flushed ORM object."""
    emb = FaceEmbedding(
        track_id=track_id,
        cluster_id=cluster_id,
        entity_type=entity_type,
        entity_id=entity_id,
        embedding=embedding,
        norm=norm,
        score=score,
        bbox=bbox,
        timestamp_s=timestamp_s,
        is_exemplar=is_exemplar,
        embedder=embedder,
    )
    session.add(emb)
    session.flush()
    return emb


def get_cluster_exemplars(
    cluster_id: int,
    session: Session | None = None,
) -> list[FaceEmbedding]:
    """Get all exemplar embeddings for a cluster."""
    def _query(sess: Session) -> list[FaceEmbedding]:
        stmt = (
            select(FaceEmbedding)
            .where(
                FaceEmbedding.cluster_id == cluster_id,
                FaceEmbedding.is_exemplar.is_(True),
            )
            .order_by(FaceEmbedding.score.desc())
        )
        return list(sess.execute(stmt).scalars().all())

    if session is not None:
        return _query(session)

    with get_session_local()() as sess:
        return _query(sess)


def get_cluster_embeddings(
    cluster_id: int,
    *,
    exemplars_only: bool = False,
) -> list[FaceEmbedding]:
    """Retrieve embeddings for a cluster."""
    with get_session_local()() as session:
        stmt = select(FaceEmbedding).where(FaceEmbedding.cluster_id == cluster_id)
        if exemplars_only:
            stmt = stmt.where(FaceEmbedding.is_exemplar.is_(True))
        stmt = stmt.order_by(FaceEmbedding.score.desc())
        return list(session.execute(stmt).scalars().all())


async def get_cluster_embeddings_async(
    cluster_id: int, **kwargs: Any,
) -> list[FaceEmbedding]:
    return await asyncio.to_thread(get_cluster_embeddings, cluster_id, **kwargs)


def get_entity_tracks(
    entity_type: str,
    entity_id: int,
    *,
    label: str | None = None,
) -> list[DetectionTrack]:
    """Get all detection tracks for an entity, optionally filtered by label."""
    with get_session_local()() as session:
        stmt = (
            select(DetectionTrack)
            .where(
                DetectionTrack.entity_type == entity_type,
                DetectionTrack.entity_id == entity_id,
            )
        )
        if label is not None:
            stmt = stmt.where(DetectionTrack.label == label)
        stmt = stmt.order_by(DetectionTrack.created_at)
        return list(session.execute(stmt).scalars().all())


async def get_entity_tracks_async(
    entity_type: str, entity_id: int, **kwargs: Any,
) -> list[DetectionTrack]:
    return await asyncio.to_thread(get_entity_tracks, entity_type, entity_id, **kwargs)


def list_clusters(
    *,
    status: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[FaceCluster], int]:
    """List face clusters with optional status filter. Returns (clusters, total)."""
    with get_session_local()() as session:
        base = select(FaceCluster).where(FaceCluster.status != "merged_away")
        count_base = select(func.count(FaceCluster.id)).where(FaceCluster.status != "merged_away")
        if status:
            base = base.where(FaceCluster.status == status)
            count_base = count_base.where(FaceCluster.status == status)

        total = session.execute(count_base).scalar() or 0
        clusters = list(
            session.execute(
                base.order_by(FaceCluster.updated_at.desc()).offset(offset).limit(limit)
            ).scalars().all()
        )
        return clusters, total


async def list_clusters_async(**kwargs: Any) -> tuple[list[FaceCluster], int]:
    return await asyncio.to_thread(list_clusters, **kwargs)


def get_cluster_by_id(cluster_id: int) -> FaceCluster | None:
    """Fetch a single cluster by ID."""
    with get_session_local()() as session:
        return session.get(FaceCluster, cluster_id)


async def get_cluster_by_id_async(cluster_id: int) -> FaceCluster | None:
    return await asyncio.to_thread(get_cluster_by_id, cluster_id)


# ---------------------------------------------------------------------------
# Exemplar management
# ---------------------------------------------------------------------------

def try_add_exemplar(
    session: Session,
    *,
    cluster_id: int,
    embedding: list[float],
    norm: float,
    score: float,
    max_exemplars: int = 10,
    dedup_threshold: float = 0.85,
) -> bool:
    """Decide whether a new embedding should become an exemplar.

    Returns True if the embedding was accepted as an exemplar.

    Rules:
    1. If the embedding is too similar (cosine >= dedup_threshold) to any
       existing exemplar, reject it.
    2. If adding it would exceed max_exemplars, evict the lowest-quality
       exemplar only if the new one is strictly better.
    """
    existing = get_cluster_exemplars(cluster_id, session=session)

    # Check similarity against all existing exemplars
    new_vec = np.array(embedding, dtype=np.float32)
    new_vec_norm = np.linalg.norm(new_vec)
    if new_vec_norm > 0:
        new_vec = new_vec / new_vec_norm

    for ex in existing:
        ex_vec = np.array(ex.embedding, dtype=np.float32)
        ex_norm = np.linalg.norm(ex_vec)
        if ex_norm > 0:
            ex_vec = ex_vec / ex_norm
        sim = float(np.dot(new_vec, ex_vec))
        if sim >= dedup_threshold:
            return False  # Too similar to existing exemplar

    if len(existing) < max_exemplars:
        return True  # Room available

    # Find worst existing exemplar
    worst = min(existing, key=lambda e: (e.score, e.norm))
    new_quality = (score, norm)
    worst_quality = (worst.score, worst.norm)

    if new_quality > worst_quality:
        # Evict worst
        session.execute(
            update(FaceEmbedding)
            .where(FaceEmbedding.id == worst.id)
            .values(is_exemplar=False)
        )
        session.flush()
        return True

    return False  # New embedding isn't better than the worst exemplar


def _recompute_exemplars(cluster_id: int) -> None:
    """After a merge, re-select the best exemplar set for a cluster.

    Keeps at most 10 exemplars, chosen greedily by quality with
    dedup filtering.
    """
    with get_session_local()() as session:
        # First, clear all exemplar flags
        session.execute(
            update(FaceEmbedding)
            .where(FaceEmbedding.cluster_id == cluster_id)
            .values(is_exemplar=False)
        )
        session.flush()

        # Get all embeddings ordered by quality
        all_embs = list(
            session.execute(
                select(FaceEmbedding)
                .where(FaceEmbedding.cluster_id == cluster_id)
                .order_by(FaceEmbedding.score.desc(), FaceEmbedding.norm.desc())
            ).scalars().all()
        )

        selected_ids: list[int] = []
        selected_vecs: list[np.ndarray] = []
        max_exemplars = 10
        dedup_threshold = 0.85

        for emb in all_embs:
            if len(selected_ids) >= max_exemplars:
                break
            vec = np.array(emb.embedding, dtype=np.float32)
            vec_norm = np.linalg.norm(vec)
            if vec_norm > 0:
                vec = vec / vec_norm

            too_similar = False
            for sv in selected_vecs:
                if float(np.dot(vec, sv)) >= dedup_threshold:
                    too_similar = True
                    break
            if too_similar:
                continue

            selected_ids.append(emb.id)
            selected_vecs.append(vec)

        if selected_ids:
            session.execute(
                update(FaceEmbedding)
                .where(FaceEmbedding.id.in_(selected_ids))
                .values(is_exemplar=True)
            )
        session.commit()
