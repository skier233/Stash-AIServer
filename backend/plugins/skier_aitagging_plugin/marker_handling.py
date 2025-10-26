from __future__ import annotations

import logging
from typing import Any, Callable, Mapping, Sequence

from .models import TagTimeFrame
from .tag_config import TagSettings, get_tag_configuration
from stash_ai_server.db.ai_results_store import get_scene_timespans_async
from stash_ai_server.utils.stash_api import stash_api

_log = logging.getLogger(__name__)


def merge_spans_for_tag(
    spans: Sequence[TagTimeFrame],
    settings: TagSettings,
    frame_interval: float | None,
) -> list[TagTimeFrame]:
    normalized = [normalize_timeframe(entry, frame_interval) for entry in spans]
    normalized = [entry for entry in normalized if _span_duration(entry) > 0.0]
    if not normalized:
        return []

    # Filter by confidence threshold (MarkerMergeParam1)
    confidence_threshold = _get_merge_param(settings, 0)
    if confidence_threshold is not None:
        normalized = [
            entry
            for entry in normalized
            if entry.confidence is None or entry.confidence >= confidence_threshold
        ]
        if not normalized:
            return []

    strategy_name = (settings.merge_strategy or "default").lower()
    strategy = _MERGE_STRATEGIES.get(strategy_name)
    if strategy is None:
        _log.warning("Unknown merge strategy '%s'; falling back to 'default'", settings.merge_strategy)
        strategy = _merge_contiguous

    merged = strategy(normalized, settings, frame_interval)

    # Filter out markers shorter than min_marker_duration
    min_duration = settings.min_marker_duration
    _log.info(f"Min marker duration: {min_duration}")
    if min_duration is not None and min_duration > 0:
        merged = [span for span in merged if _span_duration(span) >= min_duration]

    return merged


def total_span_coverage(spans: Sequence[TagTimeFrame]) -> float:
    if not spans:
        return 0.0
    ordered = sorted(spans, key=lambda frame: frame.start)
    coverage = 0.0
    current_start = ordered[0].start
    current_end = ordered[0].end if ordered[0].end is not None else ordered[0].start
    for frame in ordered[1:]:
        span_start = frame.start
        span_end = frame.end if frame.end is not None else frame.start
        if span_start <= current_end:
            current_end = max(current_end, span_end)
        else:
            coverage += max(0.0, current_end - current_start)
            current_start = span_start
            current_end = span_end
    coverage += max(0.0, current_end - current_start)
    return coverage


async def apply_scene_markers(
    *,
    scene_id: int,
    service_name: str,
) -> dict[int, list[TagTimeFrame]]:
    """
    Retrieve all stored timespans for a scene, merge them, and apply markers to Stash.
    
    Workflow:
    1. Get all stored timespans (keyed by tag_id)
    2. Merge timespans per tag using configuration
    3. Remove old markers for these tags from Stash
    4. Create new merged markers in Stash
    
    Returns:
        Dictionary mapping tag_id to lists of merged TagTimeFrame objects that were applied.
    """
    
    config = get_tag_configuration()

    try:
        stored_timespans = await get_scene_timespans_async(
            service=service_name,
            scene_id=scene_id,
        )
        if stored_timespans is None:
            return {}
        
        stored_interval, stored_map = stored_timespans
        frame_interval, timespan_map = _timespans_from_storage(stored_interval, stored_map)
    except Exception:
        _log.exception("Failed to collect timespans for scene_id=%s", scene_id)
        return {}

    if not timespan_map:
        return {}

    # Result: tag_id -> merged spans
    result: dict[int, list[TagTimeFrame]] = {}
    # Track which tag IDs we're managing
    managed_tag_ids: list[int] = []

    for category, tag_map in timespan_map.items():
        for tag_id_str, spans in tag_map.items():
            if not spans:
                continue
            
            try:
                tag_id = int(tag_id_str)
            except (ValueError, TypeError):
                _log.warning("Invalid tag_id in stored timespans: %s", tag_id_str)
                continue
            
            # Get the tag name from Stash for this tag_id
            tag_name = stash_api.get_stash_tag_name(tag_id)
            if not tag_name:
                _log.warning("Could not resolve tag name for tag_id=%s; skipping", tag_id)
                continue
            managed_tag_ids.append(tag_id)
            # Use tag name to look up config (config is keyed by backend tag names)
            # Since we already resolved backend->stash during storage, we need to find
            # which backend tag maps to this stash tag
            settings = config.resolve(tag_name)
            
            if not settings.markers_enabled:
                continue

            merged_spans = merge_spans_for_tag(spans, settings, frame_interval)
            
            if merged_spans:
                result[tag_id] = merged_spans
                


    # TODO: We should remove all existing markers that we own regardless of if they're just the ones we're recreating now
    # Apply markers to Stash
    if managed_tag_ids:
        # Remove old markers for these tags
        _log.info("Removing old markers for scene_id=%s with %d tag(s)", scene_id, len(managed_tag_ids))
        stash_api.destroy_markers_with_tags(scene_id, managed_tag_ids)
        
        # Create new markers
        markers_to_create: dict[tuple[int, str], list[tuple[float, float]]] = {}
        for tag_id, spans in result.items():
            tag_name = stash_api.get_stash_tag_name(tag_id)
            if not tag_name:
                continue
            markers_to_create[(tag_id, tag_name)] = [
                (span.start, span.end)
                for span in spans
            ]
        
        if markers_to_create:
            total_markers = sum(len(spans) for spans in markers_to_create.values())
            _log.info("Creating %d markers for scene_id=%s", total_markers, scene_id)
            stash_api.create_scene_markers(scene_id, markers_to_create)

    return result


def _merge_contiguous(
    spans: Sequence[TagTimeFrame],
    settings: TagSettings,
    frame_interval: float | None,
) -> list[TagTimeFrame]:
    """
    Merge contiguous or nearby spans with intelligent gap handling.
    
    Gap calculation:
    allowed_gap = max(max_gap, min(length_based_gap, max_factor_gap))
    
    Where:
    - max_gap: minimum gap threshold (from settings.max_gap)
    - length_based_gap: gap_length_factor * max(current_span_length, next_span_length)
    - max_factor_gap: maximum allowed gap from factor calculation (merge_params[2])
    - gap_length_factor: multiplier for length-based gaps (merge_params[1], default 0.5)
    
    This allows short spans to merge with small gaps, while longer spans can
    tolerate proportionally larger gaps, within reasonable bounds.
    """
    if not spans:
        return []
    
    # Base gap threshold
    max_gap = _safe_float(settings.max_gap) or 0.0
    if max_gap < 0:
        max_gap = 0.0
    
    # Length-based gap parameters
    gap_length_factor = _get_merge_param(settings, 1)  # Param index 1 (MarkerMergeParam2)
    if gap_length_factor is None:
        gap_length_factor = 0.5  # Default: allow gap up to 50% of span length
    
    max_factor_gap = _get_merge_param(settings, 2)  # Param index 2 (MarkerMergeParam3)
    if max_factor_gap is None:
        max_factor_gap = 10.0  # Default: cap length-based gaps at 10 seconds
    if max_factor_gap is None:
        max_factor_gap = 10.0  # Default: cap length-based gaps at 10 seconds

    merged: list[TagTimeFrame] = []
    current_start = spans[0].start
    current_end = spans[0].end if spans[0].end is not None else spans[0].start
    confidences = _confidence_list(spans[0].confidence)

    for frame in spans[1:]:
        span_end = frame.end if frame.end is not None else frame.start
        gap = frame.start - current_end
        
        # Calculate current span length
        current_length = current_end - current_start
        
        # Calculate next span length
        next_length = span_end - frame.start
        
        # Length-based gap: proportional to the longer of the two spans
        longer_span = max(current_length, next_length)
        length_based_gap = gap_length_factor * longer_span
        
        # Constrain length-based gap to max_factor_gap
        constrained_length_gap = min(length_based_gap, max_factor_gap)
        
        # Final allowed gap is the maximum of base threshold and constrained length-based gap
        allowed_gap = max(max_gap, constrained_length_gap)
        
        if gap <= allowed_gap:
            # Merge: extend current span
            current_end = max(current_end, span_end)
            if frame.confidence is not None:
                confidences.append(frame.confidence)
            continue
        
        # Gap too large: finalize current span and start new one
        merged.append(_build_frame(current_start, current_end, confidences))
        current_start = frame.start
        current_end = span_end
        confidences = _confidence_list(frame.confidence)

    merged.append(_build_frame(current_start, current_end, confidences))
    return merged


def _no_merge_strategy(
    spans: Sequence[TagTimeFrame],
    settings: TagSettings,
    frame_interval: float | None,
) -> list[TagTimeFrame]:
    return [normalize_timeframe(frame, frame_interval) for frame in spans]


def normalize_timeframe(frame: TagTimeFrame, frame_interval: float | None) -> TagTimeFrame:
    start_value = float(frame.start or 0.0)
    end_value = float(frame.end)
    confidence = _safe_float(frame.confidence)
    return TagTimeFrame(start=start_value, end=end_value, confidence=confidence)


def _build_frame(start: float, end: float, confidences: list[float]) -> TagTimeFrame:
    confidence = None
    if confidences:
        confidence = sum(confidences) / len(confidences)
    return TagTimeFrame(start=start, end=end, confidence=confidence)


def _confidence_list(value: float | None) -> list[float]:
    if value is None:
        return []
    parsed = _safe_float(value)
    if parsed is None:
        return []
    return [parsed]


def _span_duration(frame: TagTimeFrame) -> float:
    end_value = frame.end if frame.end is not None else frame.start
    return max(0.0, end_value - frame.start)


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _get_merge_param(settings: TagSettings, index: int) -> float | None:
    if index < 0 or index >= len(settings.merge_params):
        return None
    return settings.merge_params[index]


def _timespans_from_storage(
    frame_interval: float | None,
    raw_timespans: Mapping[str | None, Mapping[str | None, Sequence[Mapping[str, Any]]]] | None,
) -> tuple[float | None, dict[str | None, dict[str, list[TagTimeFrame]]]]:
    result: dict[str | None, dict[str, list[TagTimeFrame]]] = {}
    if not raw_timespans:
        return frame_interval, result
    for category, tag_map in raw_timespans.items():
        if not isinstance(tag_map, Mapping):
            continue
        category_key = _normalize_category(category)
        bucket = result.setdefault(category_key, {})
        for label_key, spans in tag_map.items():
            if not spans:
                continue
            label_value = _normalize_label(label_key)
            if label_value is None:
                continue
            entries: list[TagTimeFrame] = []
            for span in spans:
                frame = _coerce_timeframe(span)
                if frame is not None:
                    entries.append(frame)
            if entries:
                bucket[label_value] = entries
        if not bucket:
            result.pop(category_key, None)
    return frame_interval, result


def _normalize_category(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_label(label: object) -> str | None:
    if label is None:
        return None
    text = str(label).strip()
    return text or None


def _coerce_timeframe(value: Any) -> TagTimeFrame | None:
    if isinstance(value, TagTimeFrame):
        return value
    if isinstance(value, Mapping):
        start = _safe_float(value.get("start"))
        if start is None:
            return None
        end = _safe_float(value.get("end"))
        confidence = _safe_float(value.get("confidence"))
        return TagTimeFrame(start=start, end=end, confidence=confidence)
    start = _safe_float(getattr(value, "start", None))
    if start is None:
        return None
    end = _safe_float(getattr(value, "end", None))
    confidence = _safe_float(getattr(value, "confidence", None))
    return TagTimeFrame(start=start, end=end, confidence=confidence)


StrategyFn = Callable[[Sequence[TagTimeFrame], TagSettings, float | None], list[TagTimeFrame]]


_MERGE_STRATEGIES: dict[str, StrategyFn] = {
    "default": _merge_contiguous,
    "contiguous": _merge_contiguous,
    "none": _no_merge_strategy,
}


__all__ = [
    "merge_spans_for_tag",
    "total_span_coverage",
    "normalize_timeframe",
    "apply_scene_markers",
]
