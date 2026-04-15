"""Debug API endpoints for the engagement scoring subsystem.

Provides inspection endpoints to view engagement scores, signal breakdowns,
segment heatmaps, and per-scene detail.  Intended for development and
tuning; can be gated or removed before production release.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query

from stash_ai_server.core.api_key import require_shared_api_key
from stash_ai_server.recommendations.engagement.scorer import (
    score_scenes,
    score_all_watched_scenes,
)
from stash_ai_server.recommendations.engagement.segment_scorer import (
    compute_segment_heatmap,
)
from stash_ai_server.recommendations.engagement.signals import SIGNALS

_log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/engagement",
    tags=["engagement-debug"],
    dependencies=[Depends(require_shared_api_key)],
)


def _result_to_dict(r: Any) -> dict:
    """Serialize an EngagementResult to a JSON-friendly dict."""
    return {
        "entity_id": r.entity_id,
        "entity_type": r.entity_type,
        "score": round(r.score, 4),
        "confidence": round(r.confidence, 4),
        "signal_count": r.signal_count,
        "total_possible": r.total_possible,
        "signals": {
            name: {
                "value": round(sv.value, 4) if sv.available else None,
                "raw": sv.raw,
                "available": sv.available,
                "reliability": sv.reliability,
                "source": sv.source,
                "weight": sv.weight,
                "effective_contribution": round(
                    sv.value * sv.reliability * sv.weight, 4
                ) if sv.available else 0,
            }
            for name, sv in r.signals.items()
        },
    }


@router.get("/signals")
async def list_signals() -> dict:
    """List all defined engagement signals with their reliability ratings."""
    return {
        "signals": [
            {
                "name": s.name,
                "reliability": s.reliability,
                "description": s.description,
            }
            for s in SIGNALS
        ]
    }


@router.get("/scores")
async def get_engagement_scores(
    scene_ids: str = Query(
        ..., description="Comma-separated scene IDs"
    ),
    weights: str | None = Query(
        None,
        description="Optional weight overrides: signal_name:weight,signal_name:weight",
    ),
) -> dict:
    """Get engagement scores for specific scenes with full signal breakdown."""
    ids = [int(x.strip()) for x in scene_ids.split(",") if x.strip().isdigit()]
    if not ids:
        return {"scores": [], "error": "No valid scene IDs provided"}

    weight_overrides = _parse_weights(weights)

    results = score_scenes(
        ids,
        weight_overrides=weight_overrides,
    )

    return {
        "scores": [_result_to_dict(results[sid]) for sid in ids if sid in results],
        "count": len(results),
    }


@router.get("/top")
async def get_top_engaged_scenes(
    limit: int = Query(50, ge=1, le=500),
    min_score: float = Query(0.0, ge=0.0, le=1.0),
    min_confidence: float = Query(0.0, ge=0.0, le=1.0),
    weights: str | None = Query(None),
) -> dict:
    """Get the user's top engaged scenes, sorted by score descending.

    Useful for debugging whether engagement scoring matches intuition.
    """
    weight_overrides = _parse_weights(weights)

    results = score_all_watched_scenes(
        weight_overrides=weight_overrides,
        limit=limit,
        min_score=min_score,
        min_confidence=min_confidence,
    )

    return {
        "scenes": [_result_to_dict(r) for r in results],
        "count": len(results),
    }


@router.get("/scenes/{scene_id}/segments")
async def get_scene_segments(
    scene_id: int,
    bin_size: float = Query(10.0, ge=1.0, le=120.0),
    duration: float | None = Query(None, description="Scene duration override"),
) -> dict:
    """Get segment-level engagement heatmap for a single scene.

    Shows which parts of the video were watched/rewatched most.
    """
    heatmap = compute_segment_heatmap(
        scene_id,
        scene_duration=duration,
        bin_size_s=bin_size,
    )

    return {
        "scene_id": scene_id,
        "bin_size_s": bin_size,
        "segments": [
            {
                "start_s": round(seg.start_s, 1),
                "end_s": round(seg.end_s, 1),
                "score": round(seg.score, 4),
                "watch_count": seg.watch_count,
                "total_watch_s": round(seg.total_watch_s, 1),
            }
            for seg in heatmap
        ],
        "total_segments": len(heatmap),
    }


@router.get("/scenes/{scene_id}/detail")
async def get_scene_engagement_detail(
    scene_id: int,
    bin_size: float = Query(10.0, ge=1.0, le=120.0),
    weights: str | None = Query(None),
) -> dict:
    """Get combined scene-level score + segment heatmap for a single scene."""
    weight_overrides = _parse_weights(weights)

    results = score_scenes(
        [scene_id],
        weight_overrides=weight_overrides,
    )
    engagement = results.get(scene_id)

    heatmap = compute_segment_heatmap(scene_id, bin_size_s=bin_size)

    response: dict[str, Any] = {
        "scene_id": scene_id,
    }

    if engagement:
        response["engagement"] = _result_to_dict(engagement)
    else:
        response["engagement"] = None

    response["segments"] = [
        {
            "start_s": round(seg.start_s, 1),
            "end_s": round(seg.end_s, 1),
            "score": round(seg.score, 4),
            "watch_count": seg.watch_count,
            "total_watch_s": round(seg.total_watch_s, 1),
        }
        for seg in heatmap
    ]

    return response


def _parse_weights(raw: str | None) -> dict[str, float] | None:
    """Parse weight override string like 'rating:2.0,recency:0.5'."""
    if not raw:
        return None
    overrides: dict[str, float] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if ":" not in pair:
            continue
        name, val_str = pair.split(":", 1)
        name = name.strip()
        try:
            overrides[name] = float(val_str.strip())
        except ValueError:
            continue
    return overrides if overrides else None
