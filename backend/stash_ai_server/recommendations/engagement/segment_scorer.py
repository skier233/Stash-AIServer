"""Segment-level engagement scoring.

Overlays watch segment data onto a scene's timeline to produce a per-region
engagement heatmap.  Can align with embedding sections that have start/end
times.
"""
from __future__ import annotations

import logging
from typing import Any, Sequence

import sqlalchemy as sa

from stash_ai_server.db.session import get_session_local
from stash_ai_server.models.entity_embeddings import EntityEmbedding

from .data_loader import load_scene_watch_segments
from .types import SegmentScore, WeightedEmbedding

_log = logging.getLogger(__name__)


def compute_segment_heatmap(
    scene_id: int,
    scene_duration: float | None = None,
    *,
    bin_size_s: float = 10.0,
) -> list[SegmentScore]:
    """Build an engagement heatmap for a scene by overlaying all watch segments.

    Divides the scene into fixed-size bins and counts how many watch segments
    overlap each bin (rewatch density).  Scores are normalized 0–1 relative
    to the bin with the highest density.

    When the last segment in a session is identifiable (latest end_s per session
    is not tracked here — we use a heuristic: the segment with the largest end_s
    gets a small bonus as it's likely the "finish" point), it receives a weight
    boost since the user chose to stop there (potentially having reached climax).
    """
    segments = load_scene_watch_segments(scene_id)
    if not segments:
        return []

    # Determine scene length from data if not provided
    if not scene_duration or scene_duration <= 0:
        max_end = max((s["end_s"] for s in segments), default=0)
        if max_end <= 0:
            return []
        scene_duration = max_end + bin_size_s  # pad slightly beyond last segment

    num_bins = max(1, int(scene_duration / bin_size_s) + 1)

    # Accumulate watch density per bin
    bin_counts = [0.0] * num_bins
    bin_watch_s = [0.0] * num_bins

    # Find the latest segment end (heuristic for "last watched point")
    latest_end = max((s["end_s"] for s in segments), default=0)

    for seg in segments:
        start = seg["start_s"]
        end = seg["end_s"]
        watched = seg["watched_s"]

        if end <= start:
            continue

        # Determine bin overlap
        first_bin = max(0, int(start / bin_size_s))
        last_bin = min(num_bins - 1, int(end / bin_size_s))

        # Weight boost for the last-watched segment
        is_latest = abs(end - latest_end) < 1.0
        weight = 1.5 if is_latest else 1.0

        for b in range(first_bin, last_bin + 1):
            bin_start = b * bin_size_s
            bin_end = bin_start + bin_size_s
            overlap_start = max(start, bin_start)
            overlap_end = min(end, bin_end)
            overlap = max(0.0, overlap_end - overlap_start)
            if overlap > 0:
                fraction = overlap / (end - start) if (end - start) > 0 else 0
                bin_counts[b] += weight
                bin_watch_s[b] += watched * fraction

    # Normalize to 0–1
    max_count = max(bin_counts) if bin_counts else 1.0
    if max_count <= 0:
        max_count = 1.0

    result: list[SegmentScore] = []
    for b in range(num_bins):
        if bin_counts[b] <= 0:
            continue
        result.append(SegmentScore(
            start_s=b * bin_size_s,
            end_s=min((b + 1) * bin_size_s, scene_duration),
            score=bin_counts[b] / max_count,
            watch_count=int(bin_counts[b]),
            total_watch_s=bin_watch_s[b],
        ))

    return result


def get_engagement_weighted_embeddings(
    scene_id: int,
    embedding_type_prefix: str = "visual_dinov3",
    *,
    entity_type: str = "scene",
    scene_duration: float | None = None,
    top_k: int | None = None,
    min_score: float = 0.0,
    bin_size_s: float = 10.0,
) -> list[WeightedEmbedding]:
    """Return embeddings from a scene weighted by segment engagement scores.

    Loads the segment heatmap, then aligns each embedding's time range
    (start_time/end_time) with the heatmap to compute its engagement weight.
    """
    heatmap = compute_segment_heatmap(
        scene_id, scene_duration, bin_size_s=bin_size_s,
    )
    if not heatmap:
        # No watch data — return all embeddings with uniform weight
        return _load_embeddings_uniform(scene_id, embedding_type_prefix, entity_type)

    # Load matching embeddings
    try:
        with get_session_local()() as session:
            rows = session.execute(
                sa.select(EntityEmbedding).where(
                    EntityEmbedding.entity_type == entity_type,
                    EntityEmbedding.entity_id == scene_id,
                    EntityEmbedding.embedding_type.like(f"{embedding_type_prefix}%"),
                )
            ).scalars().all()

            if not rows:
                return []

            results: list[WeightedEmbedding] = []
            for emb in rows:
                start = emb.start_time or 0.0
                end = emb.end_time or (scene_duration or 0.0)
                if end <= start:
                    end = start + bin_size_s

                # Compute average heatmap score over this embedding's time range
                total_score = 0.0
                overlap_count = 0
                for seg in heatmap:
                    ov_start = max(start, seg.start_s)
                    ov_end = min(end, seg.end_s)
                    if ov_end > ov_start:
                        total_score += seg.score
                        overlap_count += 1

                weight = total_score / overlap_count if overlap_count > 0 else 0.0

                if weight < min_score:
                    continue

                results.append(WeightedEmbedding(
                    embedding=list(emb.embedding),
                    weight=weight,
                    start_s=start,
                    end_s=end,
                    entity_id=scene_id,
                    entity_type=entity_type,
                    embedding_type=emb.embedding_type,
                ))

            # Sort by weight descending
            results.sort(key=lambda e: e.weight, reverse=True)
            if top_k and top_k > 0:
                results = results[:top_k]

            return results

    except Exception:
        _log.exception("Failed to load embeddings for scene %s", scene_id)
        return []


def _load_embeddings_uniform(
    scene_id: int,
    embedding_type_prefix: str,
    entity_type: str,
) -> list[WeightedEmbedding]:
    """Load embeddings with uniform weight (1.0) when no watch data exists."""
    try:
        with get_session_local()() as session:
            rows = session.execute(
                sa.select(EntityEmbedding).where(
                    EntityEmbedding.entity_type == entity_type,
                    EntityEmbedding.entity_id == scene_id,
                    EntityEmbedding.embedding_type.like(f"{embedding_type_prefix}%"),
                )
            ).scalars().all()

            return [
                WeightedEmbedding(
                    embedding=list(emb.embedding),
                    weight=1.0,
                    start_s=emb.start_time or 0.0,
                    end_s=emb.end_time or 0.0,
                    entity_id=scene_id,
                    entity_type=entity_type,
                    embedding_type=emb.embedding_type,
                )
                for emb in rows
            ]
    except Exception:
        _log.exception("Failed to load embeddings for scene %s", scene_id)
        return []
