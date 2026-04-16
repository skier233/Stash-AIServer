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
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from stash_ai_server.db.session import get_session_local
from stash_ai_server.models.detections import (
    DetectionTrack,
    FaceCluster,
    FaceEmbedding,
    FacePerformerAssignment,
    StashDBPerformerRef,
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
    cluster_id: int | None = None,
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
        cluster_id=cluster_id,
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
    rows.  After deletion, any clusters that lost embeddings but still have
    remaining rows from other entities get their centroids recomputed.
    Clusters that lost *all* embeddings keep their stale centroid so that
    a subsequent scan can still match against them.

    Returns the number of tracks deleted.
    """
    affected_cluster_ids: set[int] = set()

    with get_session_local()() as session:
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

        # Collect cluster_ids whose embeddings will be cascade-deleted
        stale_track_ids = select(DetectionTrack.id).where(
            DetectionTrack.run_id.in_(stale_run_ids),
        )
        rows = session.execute(
            select(FaceEmbedding.cluster_id)
            .where(
                FaceEmbedding.track_id.in_(stale_track_ids),
                FaceEmbedding.cluster_id.isnot(None),
            )
            .distinct()
        ).all()
        affected_cluster_ids = {r[0] for r in rows}

        result = session.execute(
            delete(DetectionTrack).where(
                DetectionTrack.run_id.in_(stale_run_ids),
            )
        )
        deleted = result.rowcount  # type: ignore[union-attr]
        session.commit()

    # Recompute centroids only for clusters that still have embeddings.
    # Clusters that lost ALL embeddings keep their stale centroid intact
    # so the next scan can still match against them.
    if affected_cluster_ids:
        with get_session_local()() as session:
            surviving = set(
                r[0] for r in session.execute(
                    select(FaceEmbedding.cluster_id)
                    .where(FaceEmbedding.cluster_id.in_(list(affected_cluster_ids)))
                    .distinct()
                ).all()
            )
        for cid in surviving:
            try:
                _recompute_exemplars(cid)
                update_cluster_centroid(cid)
            except Exception:
                _log.debug(
                    "Failed to recompute cluster %d after stale cleanup",
                    cid, exc_info=True,
                )

    return deleted


async def cleanup_stale_detections_async(**kwargs: Any) -> int:
    return await asyncio.to_thread(cleanup_stale_detections, **kwargs)


# ---------------------------------------------------------------------------
# Face Clusters
# ---------------------------------------------------------------------------

def find_nearest_cluster(
    embedding: list[float] | np.ndarray,
    *,
    exclude_statuses: Sequence[str] = ("merged_away",),
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


def _compute_quality_score(
    sample_count: int,
    mean_norm: float,
    mean_score: float,
) -> float:
    """Compute a 0–1 quality score for a face cluster.

    Weights are calibrated from empirical analysis of identified vs ignored
    clusters (56 identified, 75 ignored).  The three factors are:

    * **Sample factor** (40 %): ``1 - 1/n``.  Single-detection clusters
      score 0; this alone rejects 83 % of junk with 0 % false positives.
    * **Norm factor** (35 %): mean embedding L2-norm mapped linearly
      from [18, 26] → [0, 1].  Identified-cluster 5th-percentile is 20.5
      vs 18.2 for ignored.
    * **Score factor** (25 %): mean detection confidence mapped linearly
      from [0.65, 0.95] → [0, 1].
    """
    sample_f = 1.0 - 1.0 / max(sample_count, 1)
    norm_f = max(0.0, min(1.0, (mean_norm - 18.0) / 8.0))
    score_f = max(0.0, min(1.0, (mean_score - 0.65) / 0.30))
    return round(0.40 * sample_f + 0.35 * norm_f + 0.25 * score_f, 4)


def update_cluster_centroid(cluster_id: int) -> None:
    """Recompute the centroid and quality_score of a cluster."""
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
                .values(centroid=None, sample_count=0, quality_score=None,
                        updated_at=dt.datetime.now(dt.timezone.utc))
            )
            session.commit()
            return

        vectors = [np.array(row[0], dtype=np.float32) for row in rows]
        mean_vec = np.mean(vectors, axis=0)
        # L2-normalise the centroid
        norm = np.linalg.norm(mean_vec)
        if norm > 0:
            mean_vec = mean_vec / norm

        # Aggregate quality metrics across ALL embeddings in this cluster
        stats_row = session.execute(
            select(
                func.avg(FaceEmbedding.norm),
                func.avg(FaceEmbedding.score),
            )
            .where(FaceEmbedding.cluster_id == cluster_id)
        ).one()
        mean_emb_norm = float(stats_row[0] or 0.0)
        mean_det_score = float(stats_row[1] or 0.0)

        quality = _compute_quality_score(len(vectors), mean_emb_norm, mean_det_score)

        session.execute(
            update(FaceCluster)
            .where(FaceCluster.id == cluster_id)
            .values(
                centroid=mean_vec.tolist(),
                sample_count=len(vectors),
                quality_score=quality,
                updated_at=dt.datetime.now(dt.timezone.utc),
            )
        )
        session.commit()


async def update_cluster_centroid_async(cluster_id: int) -> None:
    await asyncio.to_thread(update_cluster_centroid, cluster_id)


def link_performer(cluster_id: int, performer_id: int, *, label: str | None = None) -> int:
    """Set performer_id and status='identified' on a cluster.

    If *label* is provided it is stored as the cluster's display name so the
    UI can show the performer name without a separate lookup.

    Returns the number of rows updated (should be 1 on success).
    """
    values: dict = {
        "performer_id": performer_id,
        "status": "identified",
        "updated_at": dt.datetime.now(dt.timezone.utc),
    }
    if label is not None:
        values["label"] = label
    with get_session_local()() as session:
        result = session.execute(
            update(FaceCluster)
            .where(FaceCluster.id == cluster_id)
            .values(**values)
        )
        session.commit()
        rows = result.rowcount
        if rows == 0:
            _log.warning(
                "link_performer: UPDATE matched 0 rows for cluster_id=%s performer_id=%s",
                cluster_id, performer_id,
            )
        else:
            _log.debug("link_performer: cluster %s -> performer %s (%d rows)", cluster_id, performer_id, rows)
        return rows


async def link_performer_async(cluster_id: int, performer_id: int, *, label: str | None = None) -> None:
    await asyncio.to_thread(link_performer, cluster_id, performer_id, label=label)


def unlink_performer(cluster_id: int) -> None:
    """Remove performer link from a cluster, resetting it to 'unidentified'."""
    with get_session_local()() as session:
        session.execute(
            update(FaceCluster)
            .where(FaceCluster.id == cluster_id)
            .values(
                performer_id=None,
                label=None,
                status="unidentified",
                updated_at=dt.datetime.now(dt.timezone.utc),
            )
        )
        session.commit()


async def unlink_performer_async(cluster_id: int) -> None:
    await asyncio.to_thread(unlink_performer, cluster_id)


def delete_cluster(cluster_id: int) -> None:
    """Permanently delete a face cluster and all associated data.

    Removes: face_embeddings, detection_tracks, face_performer_assignments,
    and the cluster row itself.  Caller is responsible for deleting
    thumbnail files on disk (see ``_invalidate_cluster_thumbnails``).
    """
    with get_session_local()() as session:
        # 1. Delete embeddings belonging to this cluster
        session.execute(
            delete(FaceEmbedding).where(FaceEmbedding.cluster_id == cluster_id)
        )
        # 2. Delete performer assignments for this cluster
        session.execute(
            delete(FacePerformerAssignment)
            .where(FacePerformerAssignment.cluster_id == cluster_id)
        )
        # 3. Unlink detection tracks (SET NULL, don't delete the tracks
        #    themselves — they belong to the model run)
        session.execute(
            update(DetectionTrack)
            .where(DetectionTrack.cluster_id == cluster_id)
            .values(cluster_id=None)
        )
        # 4. Clear merged_into_id references from other clusters
        session.execute(
            update(FaceCluster)
            .where(FaceCluster.merged_into_id == cluster_id)
            .values(merged_into_id=None)
        )
        # 5. Delete the cluster itself
        session.execute(
            delete(FaceCluster).where(FaceCluster.id == cluster_id)
        )
        session.commit()
    _log.info("Deleted cluster %d and all associated data", cluster_id)


async def delete_cluster_async(cluster_id: int) -> None:
    await asyncio.to_thread(delete_cluster, cluster_id)


def delete_clusters_bulk(cluster_ids: list[int]) -> int:
    """Permanently delete multiple clusters in one transaction.

    Returns the number of clusters actually deleted.
    """
    if not cluster_ids:
        return 0
    with get_session_local()() as session:
        session.execute(
            delete(FaceEmbedding)
            .where(FaceEmbedding.cluster_id.in_(cluster_ids))
        )
        session.execute(
            delete(FacePerformerAssignment)
            .where(FacePerformerAssignment.cluster_id.in_(cluster_ids))
        )
        session.execute(
            update(DetectionTrack)
            .where(DetectionTrack.cluster_id.in_(cluster_ids))
            .values(cluster_id=None)
        )
        session.execute(
            update(FaceCluster)
            .where(FaceCluster.merged_into_id.in_(cluster_ids))
            .values(merged_into_id=None)
        )
        result = session.execute(
            delete(FaceCluster).where(FaceCluster.id.in_(cluster_ids))
        )
        session.commit()
    count = result.rowcount
    _log.info("Bulk-deleted %d clusters", count)
    return count


async def delete_clusters_bulk_async(cluster_ids: list[int]) -> int:
    return await asyncio.to_thread(delete_clusters_bulk, cluster_ids)


def merge_clusters(surviving_id: int, absorbed_id: int) -> None:
    """Merge one cluster into another.

    Re-parents all embeddings **and detection tracks** to the surviving
    cluster, marks the absorbed cluster as ``merged_away``, and recomputes
    the surviving cluster's centroid.

    If the surviving cluster has no performer link but the absorbed cluster
    does, the performer link (and label) are transferred to the survivor so
    that merging into a linked face doesn't silently lose the association.
    """
    with get_session_local()() as session:
        # Read both clusters so we can decide whether to transfer a link
        surviving = session.get(FaceCluster, surviving_id)
        absorbed = session.get(FaceCluster, absorbed_id)

        # Transfer performer link when the survivor has none but absorbed does
        if surviving and absorbed:
            if not surviving.performer_id and absorbed.performer_id:
                surviving.performer_id = absorbed.performer_id
                surviving.label = absorbed.label
                surviving.status = "identified"
                _log.info(
                    "merge_clusters: transferred performer_id=%s label=%r from "
                    "absorbed cluster %d to surviving cluster %d",
                    absorbed.performer_id, absorbed.label,
                    absorbed_id, surviving_id,
                )

        # Re-parent embeddings
        session.execute(
            update(FaceEmbedding)
            .where(FaceEmbedding.cluster_id == absorbed_id)
            .values(cluster_id=surviving_id)
        )
        # Re-parent detection tracks
        session.execute(
            update(DetectionTrack)
            .where(DetectionTrack.cluster_id == absorbed_id)
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


def count_cluster_embeddings(cluster_id: int) -> int:
    """Return the total number of embeddings stored for a cluster."""
    with get_session_local()() as session:
        return session.execute(
            select(func.count()).where(FaceEmbedding.cluster_id == cluster_id)
        ).scalar() or 0


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


# Allowed sort columns for list_clusters
_SORT_COLUMNS = {
    "updated_at": FaceCluster.updated_at,
    "created_at": FaceCluster.created_at,
    "sample_count": FaceCluster.sample_count,
    "quality_score": FaceCluster.quality_score,
    "label": FaceCluster.label,
    "id": FaceCluster.id,
}


def list_clusters(
    *,
    status: str | None = None,
    search: str | None = None,
    performer_id: int | None = None,
    sort: str | None = None,
    sort_dir: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[FaceCluster], int]:
    """List face clusters with optional status filter, search, and sort.

    *search* filters to clusters whose ``label`` contains the given text
    (case-insensitive).

    *sort* may be one of: updated_at, created_at, sample_count,
    quality_score, label, id.  *sort_dir* is ``asc`` or ``desc``
    (default ``desc``).
    """
    with get_session_local()() as session:
        base = select(FaceCluster).where(FaceCluster.status != "merged_away")
        count_base = select(func.count(FaceCluster.id)).where(FaceCluster.status != "merged_away")
        if status:
            base = base.where(FaceCluster.status == status)
            count_base = count_base.where(FaceCluster.status == status)
        if performer_id is not None:
            base = base.where(FaceCluster.performer_id == performer_id)
            count_base = count_base.where(FaceCluster.performer_id == performer_id)
        if search:
            pattern = f"%{search}%"
            # Match clusters by label OR by StashDB suggested performer name
            search_filter = sa.or_(
                FaceCluster.label.ilike(pattern),
                FaceCluster.stashdb_match_id.in_(
                    select(StashDBPerformerRef.id).where(
                        StashDBPerformerRef.name.ilike(pattern)
                    )
                ),
            )
            base = base.where(search_filter)
            count_base = count_base.where(search_filter)

        # Determine sort column + direction
        sort_col = _SORT_COLUMNS.get(sort or "updated_at", FaceCluster.updated_at)
        order = sort_col.asc() if sort_dir == "asc" else sort_col.desc()

        total = session.execute(count_base).scalar() or 0
        clusters = list(
            session.execute(
                base.order_by(order).offset(offset).limit(limit)
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
# Cluster query helpers (for co-occurrence / UI)
# ---------------------------------------------------------------------------

def get_cluster_entity_pairs(cluster_id: int) -> list[tuple[str, int]]:
    """Return all unique (entity_type, entity_id) rows for a cluster's tracks."""
    with get_session_local()() as session:
        stmt = (
            select(
                DetectionTrack.entity_type,
                DetectionTrack.entity_id,
            )
            .where(DetectionTrack.cluster_id == cluster_id)
            .distinct()
        )
        return [(row.entity_type, row.entity_id) for row in session.execute(stmt).all()]


async def get_cluster_entity_pairs_async(cluster_id: int) -> list[tuple[str, int]]:
    return await asyncio.to_thread(get_cluster_entity_pairs, cluster_id)


def get_bulk_cluster_entity_pairs(
    cluster_ids: list[int],
) -> dict[int, list[tuple[str, int]]]:
    """Return entity pairs for multiple clusters in a single query.

    Returns ``{cluster_id: [(entity_type, entity_id), ...]}``.
    """
    if not cluster_ids:
        return {}
    with get_session_local()() as session:
        stmt = (
            select(
                DetectionTrack.cluster_id,
                DetectionTrack.entity_type,
                DetectionTrack.entity_id,
            )
            .where(
                DetectionTrack.cluster_id.in_(cluster_ids),
                DetectionTrack.cluster_id.isnot(None),
            )
            .distinct()
        )
        result: dict[int, list[tuple[str, int]]] = {cid: [] for cid in cluster_ids}
        for row in session.execute(stmt).all():
            result[row.cluster_id].append((row.entity_type, row.entity_id))
        return result


def list_cluster_ids(
    *,
    status: str | None = None,
    search: str | None = None,
    performer_id: int | None = None,
) -> list[int]:
    """Return IDs of clusters matching *status*/*search* filters (no pagination)."""
    with get_session_local()() as session:
        stmt = select(FaceCluster.id).where(FaceCluster.status != "merged_away")
        if status:
            stmt = stmt.where(FaceCluster.status == status)
        if performer_id is not None:
            stmt = stmt.where(FaceCluster.performer_id == performer_id)
        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(
                sa.or_(
                    FaceCluster.label.ilike(pattern),
                    FaceCluster.stashdb_match_id.in_(
                        select(StashDBPerformerRef.id).where(
                            StashDBPerformerRef.name.ilike(pattern)
                        )
                    ),
                )
            )
        return [row[0] for row in session.execute(stmt).all()]


def get_clusters_by_ids(
    cluster_ids: list[int],
) -> list[FaceCluster]:
    """Fetch full cluster objects for the given IDs, preserving input order."""
    if not cluster_ids:
        return []
    with get_session_local()() as session:
        stmt = select(FaceCluster).where(FaceCluster.id.in_(cluster_ids))
        by_id = {c.id: c for c in session.execute(stmt).scalars().all()}
        return [by_id[cid] for cid in cluster_ids if cid in by_id]


def get_cluster_for_performer(performer_id: int) -> FaceCluster | None:
    """Return the cluster linked to the given performer, or None."""
    with get_session_local()() as session:
        stmt = (
            select(FaceCluster)
            .where(FaceCluster.performer_id == performer_id)
            .where(FaceCluster.status != "merged_away")
            .limit(1)
        )
        return session.execute(stmt).scalars().first()


async def get_cluster_for_performer_async(performer_id: int) -> FaceCluster | None:
    return await asyncio.to_thread(get_cluster_for_performer, performer_id)


def get_entity_count_by_type(cluster_id: int) -> dict[str, int]:
    """Return ``{scene_count: N, image_count: N}`` for a cluster.

    Counts via ``detection_tracks.cluster_id`` directly so that tracks
    matched to this cluster are counted even when the per-cluster embedding
    budget was exhausted and no embeddings were stored for that track.
    """
    with get_session_local()() as session:
        stmt = (
            select(
                DetectionTrack.entity_type,
                func.count(sa.distinct(DetectionTrack.entity_id)).label("cnt"),
            )
            .where(DetectionTrack.cluster_id == cluster_id)
            .group_by(DetectionTrack.entity_type)
        )
        rows = {row.entity_type: row.cnt for row in session.execute(stmt).all()}
        return {
            "scene_count": rows.get("scene", 0),
            "image_count": rows.get("image", 0),
        }


async def get_entity_count_by_type_async(cluster_id: int) -> dict[str, int]:
    return await asyncio.to_thread(get_entity_count_by_type, cluster_id)


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
    entity_type: str | None = None,
    entity_id: int | None = None,
    max_per_entity: int = 4,
) -> bool:
    """Decide whether a new embedding should become an exemplar.

    Returns True if the embedding was accepted as an exemplar.

    Rules:
    1. If the embedding is too similar (cosine >= dedup_threshold) to any
       existing exemplar, reject it.
    2. If adding it would exceed max_exemplars, evict the lowest-quality
       exemplar — preferring to evict from the most over-represented
       entity so exemplars are distributed across different scenes/images.
    3. If the new embedding's entity already has *max_per_entity* exemplars,
       only accept it if it beats the worst exemplar from that same entity.
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

    # --- Entity-aware eviction ---
    new_quality = (score, norm)

    # Build per-entity counts
    entity_counts: dict[tuple[str, int], list[FaceEmbedding]] = {}
    for ex in existing:
        key = (ex.entity_type, ex.entity_id)
        entity_counts.setdefault(key, []).append(ex)

    new_key = (entity_type, entity_id) if entity_type and entity_id else None

    # If the new embedding's entity already has max_per_entity exemplars,
    # only accept if it beats the worst from *that* entity.
    if new_key and new_key in entity_counts:
        same_entity = entity_counts[new_key]
        if len(same_entity) >= max_per_entity:
            worst_same = min(same_entity, key=lambda e: (e.score, e.norm))
            if new_quality <= (worst_same.score, worst_same.norm):
                return False
            # Evict worst from same over-represented entity
            session.execute(
                update(FaceEmbedding)
                .where(FaceEmbedding.id == worst_same.id)
                .values(is_exemplar=False)
            )
            session.flush()
            return True

    # Find the most over-represented entity and evict its worst member,
    # but only if the new embedding is better than the global worst.
    worst_global = min(existing, key=lambda e: (e.score, e.norm))
    if new_quality <= (worst_global.score, worst_global.norm):
        return False

    # Prefer evicting from the entity with the most exemplars
    most_represented_key = max(entity_counts, key=lambda k: len(entity_counts[k]))
    most_represented = entity_counts[most_represented_key]
    if len(most_represented) > 1:
        # Evict worst from the over-represented entity
        evict = min(most_represented, key=lambda e: (e.score, e.norm))
    else:
        # All entities have exactly 1 exemplar — fall back to global worst
        evict = worst_global

    session.execute(
        update(FaceEmbedding)
        .where(FaceEmbedding.id == evict.id)
        .values(is_exemplar=False)
    )
    session.flush()
    return True


def _recompute_exemplars(cluster_id: int) -> None:
    """After a merge, re-select the best exemplar set for a cluster.

    Keeps at most 10 exemplars, chosen greedily by quality with
    dedup filtering **and entity-level diversity**.  A soft cap limits
    how many exemplars can come from a single (entity_type, entity_id)
    so thumbnails are distributed across different scenes/images.
    """
    max_exemplars = 10
    dedup_threshold = 0.85
    hard_max_per_entity = 4  # absolute ceiling per entity

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

        if not all_embs:
            session.commit()
            return

        # Compute a dynamic per-entity cap based on how many unique entities
        # contributed embeddings.  E.g. 10 entities → 1-2 each, 2 entities → 4 each.
        unique_entities = {(e.entity_type, e.entity_id) for e in all_embs}
        dynamic_cap = max(2, -(-max_exemplars // len(unique_entities)))  # ceil div
        per_entity_cap = min(dynamic_cap, hard_max_per_entity)

        selected_ids: list[int] = []
        selected_vecs: list[np.ndarray] = []
        entity_counts: dict[tuple[str, int], int] = {}
        deferred: list[FaceEmbedding] = []

        # Pass 1: greedily pick from quality-ordered list, respecting
        # per-entity cap and dedup threshold.
        for emb in all_embs:
            if len(selected_ids) >= max_exemplars:
                break
            ekey = (emb.entity_type, emb.entity_id)

            vec = np.array(emb.embedding, dtype=np.float32)
            vec_norm = np.linalg.norm(vec)
            if vec_norm > 0:
                vec = vec / vec_norm

            too_similar = any(
                float(np.dot(vec, sv)) >= dedup_threshold for sv in selected_vecs
            )
            if too_similar:
                continue

            if entity_counts.get(ekey, 0) >= per_entity_cap:
                deferred.append(emb)  # save for pass 2
                continue

            selected_ids.append(emb.id)
            selected_vecs.append(vec)
            entity_counts[ekey] = entity_counts.get(ekey, 0) + 1

        # Pass 2: if we still have slots, fill from deferred (over-represented
        # entities).  This ensures we reach max_exemplars when possible.
        for emb in deferred:
            if len(selected_ids) >= max_exemplars:
                break
            vec = np.array(emb.embedding, dtype=np.float32)
            vec_norm = np.linalg.norm(vec)
            if vec_norm > 0:
                vec = vec / vec_norm

            too_similar = any(
                float(np.dot(vec, sv)) >= dedup_threshold for sv in selected_vecs
            )
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


def remove_exemplar_from_cluster(
    embedding_id: int,
    cluster_id: int,
    *,
    auto_threshold: float = 0.55,
) -> dict:
    """Remove an exemplar embedding from a cluster and recompute state.

    Everything runs in a **single** session/transaction to avoid pool
    thrashing and race conditions when the user rapidly deletes exemplars.

    Steps:
      1. Detach the embedding (set cluster_id=NULL, is_exemplar=False).
      2. Recompute centroid from remaining exemplars (inline).
      3. Re-check all non-exemplar embeddings: unassign any whose cosine
         similarity to the new centroid drops below *auto_threshold*.

    Exemplar re-selection is intentionally **skipped** so that explicitly
    removed faces are not backfilled from the non-exemplar pool.

    Returns a dict with ``removed_entities`` (count of unassigned embeddings)
    and ``remaining_exemplars`` (count after recomputation).
    """
    with get_session_local()() as session:
        emb = session.get(FaceEmbedding, embedding_id)
        if emb is None or emb.cluster_id != cluster_id:
            raise ValueError("Embedding not found or does not belong to cluster")

        # 1. Detach the target embedding
        session.execute(
            update(FaceEmbedding)
            .where(FaceEmbedding.id == embedding_id)
            .values(cluster_id=None, is_exemplar=False)
        )
        session.flush()
        _log.info(
            "Detached embedding %d from cluster %d (cluster_id→NULL, is_exemplar→False)",
            embedding_id, cluster_id,
        )

        # 2. Recompute centroid from remaining exemplars (inline, same session).
        exemplar_rows = session.execute(
            select(FaceEmbedding.embedding)
            .where(
                FaceEmbedding.cluster_id == cluster_id,
                FaceEmbedding.is_exemplar.is_(True),
            )
        ).all()

        if not exemplar_rows:
            session.execute(
                update(FaceCluster)
                .where(FaceCluster.id == cluster_id)
                .values(centroid=None, sample_count=0, quality_score=None,
                        updated_at=dt.datetime.now(dt.timezone.utc))
            )
            session.commit()
            return {"removed_entities": 0, "remaining_exemplars": 0}

        vectors = [np.array(row[0], dtype=np.float32) for row in exemplar_rows]
        mean_vec = np.mean(vectors, axis=0)
        norm = np.linalg.norm(mean_vec)
        if norm > 0:
            mean_vec = mean_vec / norm

        stats_row = session.execute(
            select(
                func.avg(FaceEmbedding.norm),
                func.avg(FaceEmbedding.score),
            )
            .where(FaceEmbedding.cluster_id == cluster_id)
        ).one()
        mean_emb_norm = float(stats_row[0] or 0.0)
        mean_det_score = float(stats_row[1] or 0.0)
        quality = _compute_quality_score(len(vectors), mean_emb_norm, mean_det_score)

        session.execute(
            update(FaceCluster)
            .where(FaceCluster.id == cluster_id)
            .values(
                centroid=mean_vec.tolist(),
                sample_count=len(vectors),
                quality_score=quality,
                updated_at=dt.datetime.now(dt.timezone.utc),
            )
        )

        # 3. Prune non-exemplar embeddings below threshold (inline).
        centroid = mean_vec  # already L2-normalised above
        non_exemplars = list(
            session.execute(
                select(FaceEmbedding)
                .where(
                    FaceEmbedding.cluster_id == cluster_id,
                    FaceEmbedding.is_exemplar.is_(False),
                )
            ).scalars().all()
        )
        to_remove: list[int] = []
        for ne in non_exemplars:
            vec = np.array(ne.embedding, dtype=np.float32)
            v_norm = np.linalg.norm(vec)
            if v_norm > 0:
                vec = vec / v_norm
            sim = float(np.dot(centroid, vec))
            if sim < auto_threshold:
                to_remove.append(ne.id)
        if to_remove:
            session.execute(
                update(FaceEmbedding)
                .where(FaceEmbedding.id.in_(to_remove))
                .values(cluster_id=None)
            )
            _log.info(
                "Pruned %d weak embeddings from cluster %d (threshold=%.2f)",
                len(to_remove), cluster_id, auto_threshold,
            )

        session.commit()

        # Count remaining exemplars (still in same session after commit)
        remaining_count = session.execute(
            select(func.count())
            .where(
                FaceEmbedding.cluster_id == cluster_id,
                FaceEmbedding.is_exemplar.is_(True),
            )
        ).scalar() or 0

    _log.info(
        "remove_exemplar_from_cluster done: cluster=%d, remaining_exemplars=%d, pruned=%d",
        cluster_id, remaining_count, len(to_remove),
    )

    return {"removed_entities": len(to_remove), "remaining_exemplars": remaining_count}


async def remove_exemplar_from_cluster_async(
    embedding_id: int, cluster_id: int, **kwargs,
) -> dict:
    return await asyncio.to_thread(
        remove_exemplar_from_cluster, embedding_id, cluster_id, **kwargs,
    )


def _prune_weak_embeddings(cluster_id: int, threshold: float) -> int:
    """Unassign non-exemplar embeddings whose similarity to the centroid
    has dropped below *threshold*.  Returns count of removed embeddings."""
    with get_session_local()() as session:
        cluster = session.get(FaceCluster, cluster_id)
        if cluster is None or cluster.centroid is None:
            return 0
        centroid = np.array(cluster.centroid, dtype=np.float32)
        c_norm = np.linalg.norm(centroid)
        if c_norm > 0:
            centroid = centroid / c_norm

        non_exemplars = list(
            session.execute(
                select(FaceEmbedding)
                .where(
                    FaceEmbedding.cluster_id == cluster_id,
                    FaceEmbedding.is_exemplar.is_(False),
                )
            ).scalars().all()
        )

        to_remove: list[int] = []
        for emb in non_exemplars:
            vec = np.array(emb.embedding, dtype=np.float32)
            v_norm = np.linalg.norm(vec)
            if v_norm > 0:
                vec = vec / v_norm
            sim = float(np.dot(centroid, vec))
            if sim < threshold:
                to_remove.append(emb.id)

        if to_remove:
            session.execute(
                update(FaceEmbedding)
                .where(FaceEmbedding.id.in_(to_remove))
                .values(cluster_id=None)
            )
            session.commit()
            _log.info(
                "Pruned %d weak embeddings from cluster %d (threshold=%.2f)",
                len(to_remove), cluster_id, threshold,
            )

        return len(to_remove)


# ---------------------------------------------------------------------------
# Performer assignment tracking
# ---------------------------------------------------------------------------

def record_performer_assignments(
    entity_pairs: list[tuple[str, int]],
    performer_id: int,
    cluster_id: int,
) -> int:
    """Record that we assigned *performer_id* to *entity_pairs* via *cluster_id*.

    Uses INSERT … ON CONFLICT DO NOTHING so duplicates are safely ignored.
    Returns the number of new rows inserted.
    """
    if not entity_pairs:
        return 0
    with get_session_local()() as session:
        inserted = 0
        for entity_type, entity_id in entity_pairs:
            try:
                session.execute(
                    pg_insert(FacePerformerAssignment.__table__)
                    .values(
                        entity_type=entity_type,
                        entity_id=entity_id,
                        performer_id=performer_id,
                        cluster_id=cluster_id,
                    )
                    .on_conflict_do_nothing(
                        constraint="uq_face_performer_assignment",
                    )
                )
                inserted += 1
            except Exception:
                _log.debug(
                    "Failed to record assignment (%s, %s, performer=%s, cluster=%s)",
                    entity_type, entity_id, performer_id, cluster_id,
                    exc_info=True,
                )
        session.commit()
        return inserted


def delete_performer_assignments_for_cluster(
    cluster_id: int,
    performer_id: int,
    entity_pairs: list[tuple[str, int]] | None = None,
) -> int:
    """Remove assignment records for *cluster_id* + *performer_id*.

    If *entity_pairs* is given, only those specific entities are deleted.
    Otherwise all assignments for the cluster+performer are removed.
    Returns the count of deleted rows.
    """
    with get_session_local()() as session:
        if entity_pairs:
            total = 0
            for entity_type, entity_id in entity_pairs:
                result = session.execute(
                    delete(FacePerformerAssignment)
                    .where(
                        FacePerformerAssignment.cluster_id == cluster_id,
                        FacePerformerAssignment.performer_id == performer_id,
                        FacePerformerAssignment.entity_type == entity_type,
                        FacePerformerAssignment.entity_id == entity_id,
                    )
                )
                total += result.rowcount
        else:
            result = session.execute(
                delete(FacePerformerAssignment)
                .where(
                    FacePerformerAssignment.cluster_id == cluster_id,
                    FacePerformerAssignment.performer_id == performer_id,
                )
            )
            total = result.rowcount
        session.commit()
        return total


def get_orphaned_performer_entities(
    performer_id: int,
    entity_pairs: list[tuple[str, int]],
) -> list[tuple[str, int]]:
    """Return entity pairs that have NO remaining assignment records for *performer_id*.

    These are entities where we originally added the performer via face
    recognition, and now no face cluster links remain to justify keeping it.
    """
    if not entity_pairs:
        return []
    orphaned: list[tuple[str, int]] = []
    with get_session_local()() as session:
        for entity_type, entity_id in entity_pairs:
            count = session.scalar(
                select(func.count())
                .select_from(FacePerformerAssignment)
                .where(
                    FacePerformerAssignment.entity_type == entity_type,
                    FacePerformerAssignment.entity_id == entity_id,
                    FacePerformerAssignment.performer_id == performer_id,
                )
            )
            if count == 0:
                orphaned.append((entity_type, entity_id))
    return orphaned
