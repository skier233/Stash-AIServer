from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

from stash_ai_server.recommendations.registry import recommender
from stash_ai_server.recommendations.models import RecContext, RecommendationRequest
from stash_ai_server.recommendations.utils.scene_fetch import fetch_scenes_by_ids
from stash_ai_server.recommendations.utils.timespan_metrics import (
    collect_tag_durations,
    compute_cooccurrence_duration,
)

DEFAULT_SERVICE_NAME = "AI_Tagging"
DEFAULT_LIMIT = 40
MAX_CANDIDATE_MULTIPLIER = 6
MIN_CANDIDATE_POOL = 120
MAX_CANDIDATE_POOL = 600


_log = logging.getLogger(__name__)


@dataclass
class TagSelectorData:
    include: List[int]
    exclude: List[int]
    constraints: Dict[int, Dict[str, Any]]
    combination: str


@dataclass
class DurationConstraint:
    tag_id: int
    min_value: float
    unit: str
    max_value: float | None = None
    require_presence: bool = True  # When True, tag must appear in the scene at all.


@dataclass
class CoOccurrenceConstraint:
    primary_tag: int
    co_tags: List[int]
    min_value: float
    unit: str
    max_value: float | None = None
    require_presence: bool = True  # When True, overlap must be > 0 to pass.


def _parse_tag_ids(raw: Any) -> List[int]:
    if not raw:
        return []
    tag_ids: List[int] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                value = item.get("id") or item.get("value")
            else:
                value = item
            try:
                tag_ids.append(int(value))
            except (TypeError, ValueError):
                continue
    else:
        try:
            tag_ids.append(int(raw))
        except (TypeError, ValueError):
            return []
    return tag_ids


def _safe_float(raw: Any, fallback: float = 0.0) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return fallback


def _normalize_combination(raw: Any) -> str:
    if not isinstance(raw, str):
        return "and"
    lowered = raw.strip().lower()
    if lowered in {"and", "or"}:
        return lowered
    if lowered == "not-applicable":
        return "and"
    return "and"


def _normalize_tag_selector(raw: Any) -> TagSelectorData:
    if not raw:
        return TagSelectorData(include=[], exclude=[], constraints={}, combination="and")
    if isinstance(raw, dict):
        include = _parse_tag_ids(raw.get("include"))
        exclude = _parse_tag_ids(raw.get("exclude"))
        constraints_raw = raw.get("constraints") or {}
        constraint_map: Dict[int, Dict[str, Any]] = {}
        if isinstance(constraints_raw, dict):
            for key, value in constraints_raw.items():
                tag_id: int | None = None
                try:
                    tag_id = int(key)
                except (TypeError, ValueError):
                    if isinstance(value, dict) and value.get("id") is not None:
                        try:
                            tag_id = int(value.get("id"))
                        except (TypeError, ValueError):
                            tag_id = None
                if tag_id is None:
                    continue
                constraint_map[tag_id] = dict(value) if isinstance(value, dict) else {}
        if constraint_map:
            include_set = {tag_id for tag_id in include}
            exclude_set = {tag_id for tag_id in exclude}
            for tag_id in constraint_map.keys():
                if tag_id not in include_set and tag_id not in exclude_set:
                    include.append(tag_id)
                    include_set.add(tag_id)
        combination = _normalize_combination(raw.get("tag_combination") or raw.get("combination"))
        return TagSelectorData(include=include, exclude=exclude, constraints=constraint_map, combination=combination)
    include = _parse_tag_ids(raw)
    return TagSelectorData(include=include, exclude=[], constraints={}, combination="and")


def _extract_duration_constraints(selector: TagSelectorData) -> List[DurationConstraint]:
    constraints: List[DurationConstraint] = []
    for tag_id in selector.include:
        raw = selector.constraints.get(tag_id)
        if not isinstance(raw, dict):
            continue
        constraint_type = (raw.get("type") or "").strip().lower()
        if constraint_type and constraint_type != "duration":
            continue
        duration_spec = raw.get("duration") if isinstance(raw.get("duration"), dict) else raw
        duration_spec = duration_spec or {}
        min_value = duration_spec.get("min")
        if min_value is None:
            min_value = duration_spec.get("minDuration")
        if min_value is None:
            min_value = duration_spec.get("min_seconds")
        if min_value is None:
            min_value = duration_spec.get("minPercent")
        min_value_float = _safe_float(min_value, 0.0)
        max_value = duration_spec.get("max")
        if max_value is None:
            max_value = duration_spec.get("maxDuration")
        if max_value is None:
            max_value = duration_spec.get("max_seconds")
        if max_value is None:
            max_value = duration_spec.get("maxPercent")
        max_value_float: float | None
        if max_value is None or max_value == "" or max_value == "∞":
            max_value_float = None
        else:
            max_value_float = _safe_float(max_value, 0.0)
        unit = str((duration_spec or {}).get("unit") or "seconds").lower()
        if unit not in {"seconds", "percent"}:
            unit = "seconds"
        constraints.append(
            DurationConstraint(
                tag_id=tag_id,
                min_value=min_value_float,
                max_value=max_value_float,
                unit=unit,
                require_presence=True,
            )
        )
    return constraints


def _extract_cooccurrence_constraints(selector: TagSelectorData) -> List[CoOccurrenceConstraint]:
    constraints: List[CoOccurrenceConstraint] = []
    for tag_id in selector.include:
        raw = selector.constraints.get(tag_id)
        if not isinstance(raw, dict):
            continue
        constraint_type = (raw.get("type") or "").strip().lower()
        if constraint_type and constraint_type != "overlap":
            continue
        overlap_spec = raw.get("overlap") if isinstance(raw.get("overlap"), dict) else raw
        overlap_spec = overlap_spec or {}
        co_tags_raw = (overlap_spec or {}).get("coTags") or (overlap_spec or {}).get("co_tags") or []
        co_tags = _parse_tag_ids(co_tags_raw)
        min_value = overlap_spec.get("minDuration")
        if min_value is None:
            min_value = overlap_spec.get("min_duration")
        if min_value is None:
            min_value = overlap_spec.get("min")
        min_value_float = _safe_float(min_value, 0.0)
        max_value = overlap_spec.get("maxDuration")
        if max_value is None:
            max_value = overlap_spec.get("max_duration")
        if max_value is None:
            max_value = overlap_spec.get("max")
        max_value_float: float | None
        if max_value is None or max_value == "" or max_value == "∞":
            max_value_float = None
        else:
            max_value_float = _safe_float(max_value, 0.0)
        unit = str((overlap_spec or {}).get("unit") or "seconds").lower()
        if unit not in {"seconds", "percent"}:
            unit = "seconds"
        constraints.append(
            CoOccurrenceConstraint(
                primary_tag=tag_id,
                co_tags=co_tags,
                min_value=min_value_float,
                max_value=max_value_float,
                unit=unit,
                require_presence=True,
            )
        )
    return constraints


def _extract_scene_duration(scene_payload: Dict[str, Any]) -> float | None:
    files = scene_payload.get("files")
    if isinstance(files, list):
        for entry in files:
            duration = entry.get("duration") if isinstance(entry, dict) else None
            if duration is not None:
                try:
                    value = float(duration)
                except (TypeError, ValueError):
                    continue
                if value > 0:
                    return value
    # fallback if duration stored at root
    duration_root = scene_payload.get("duration")
    if duration_root is not None:
        try:
            value = float(duration_root)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None
    return None


def _evaluate_duration_constraints(
    constraints: Sequence[DurationConstraint],
    combination: str,
    duration_lookup: Dict[int, float],
    scene_duration: float | None,
) -> tuple[bool, List[Dict[str, Any]]]:
    if not constraints:
        return True, []

    combination_mode = combination if combination in {"and", "or"} else "and"
    details: List[Dict[str, Any]] = []
    status_flags: List[bool] = []

    for constraint in constraints:
        seconds = float(duration_lookup.get(constraint.tag_id, 0.0))
        present = constraint.tag_id in duration_lookup and seconds > 0.0
        percent_value = (seconds / scene_duration) * 100.0 if scene_duration and scene_duration > 0 else None
        min_pass = True
        max_pass = True

        if constraint.require_presence and not present:
            min_pass = False
            max_pass = False
        else:
            if constraint.unit == "percent":
                if percent_value is None:
                    min_pass = constraint.min_value <= 0.0 and present
                    max_pass = constraint.max_value is None
                else:
                    min_pass = percent_value >= constraint.min_value
                    if constraint.max_value is not None:
                        max_pass = percent_value <= constraint.max_value
            else:
                min_pass = seconds >= constraint.min_value
                if constraint.max_value is not None:
                    max_pass = seconds <= constraint.max_value

        met = min_pass and max_pass and (present or not constraint.require_presence)
        details.append(
            {
                "tag_id": constraint.tag_id,
                "unit": constraint.unit,
                "required_min": constraint.min_value,
                "required_max": constraint.max_value,
                "seconds": seconds,
                "percent_of_scene": percent_value,
                "met": met,
            }
        )
        status_flags.append(met)

    if combination_mode == "or":
        return any(status_flags), details
    return all(status_flags), details


def _evaluate_cooccurrence_constraints(
    constraints: Sequence[CoOccurrenceConstraint],
    combination: str,
    scene_id: int,
    scene_duration: float | None,
    *,
    service_name: str,
) -> tuple[bool, List[Dict[str, Any]], float]:
    if not constraints:
        return True, [], 0.0

    combination_mode = combination if combination in {"and", "or"} else "and"
    details: List[Dict[str, Any]] = []
    status_flags: List[bool] = []
    scores: List[float] = []

    for constraint in constraints:
        tag_ids = [constraint.primary_tag] + [tag for tag in constraint.co_tags if tag is not None]
        overlap = compute_cooccurrence_duration(service=service_name, scene_id=scene_id, tag_ids=tag_ids)
        percent_value = (overlap / scene_duration) * 100.0 if scene_duration and scene_duration > 0 else None
        if constraint.unit == "percent":
            if percent_value is None:
                min_pass = constraint.min_value <= 0.0 and overlap > 0.0
                max_pass = constraint.max_value is None
            else:
                min_pass = percent_value >= constraint.min_value
                max_pass = True
                if constraint.max_value is not None:
                    max_pass = percent_value <= constraint.max_value
        else:
            min_pass = overlap >= constraint.min_value
            max_pass = True
            if constraint.max_value is not None:
                max_pass = overlap <= constraint.max_value
        met = min_pass and max_pass
        if constraint.require_presence and overlap <= 0.0:
            met = False
        details.append(
            {
                "primary_tag": constraint.primary_tag,
                "co_tags": constraint.co_tags,
                "unit": constraint.unit,
                "required_min": constraint.min_value,
                "required_max": constraint.max_value,
                "overlap_seconds": overlap,
                "percent_of_scene": percent_value,
                "met": met,
            }
        )
        status_flags.append(met)
        scores.append(overlap)

    if not scores:
        aggregate = 0.0
    elif combination_mode == "or":
        aggregate = max(scores)
    else:
        aggregate = min(scores)

    if combination_mode == "or":
        return any(status_flags), details, aggregate
    return all(status_flags), details, aggregate


def _sort_candidates(ordering: str, items: List[Dict[str, Any]]) -> None:
    ordering = (ordering or "co_duration_desc").lower()
    if ordering == "presence_sum_desc":
        items.sort(key=lambda item: item.get("presence_sum", 0.0), reverse=True)
    elif ordering == "co_duration_asc":
        items.sort(key=lambda item: item.get("co_duration_score", 0.0))
    else:
        items.sort(key=lambda item: item.get("co_duration_score", 0.0), reverse=True)


@recommender(
    id="timespan_tag_filter",
    label="Timespan Tag Filter",
    description="Filter scenes by AI timespan durations and tag co-occurrence thresholds.",
    contexts=[RecContext.global_feed],
    config=[
        {
            "name": "presence_tags",
            "label": "Tag Duration Requirements",
            "type": "tags",
            "default": [],
            "tag_combination": "and",
            "constraint_types": ["duration"],
            "allowed_combination_modes": ["or", "and"],
            "help": "Require selected tags to appear for at least the configured duration.",
        },
        {
            "name": "cooccurrence_tags",
            "label": "Co-occurrence Requirements",
            "type": "tags",
            "default": [],
            "tag_combination": "and",
            "constraint_types": ["overlap"],
            "allowed_combination_modes": ["or", "and"],
            "help": "Define groups of tags that must overlap for a minimum duration.",
        },
        {
            "name": "ordering",
            "label": "Ordering",
            "type": "enum",
            "default": "co_duration_desc",
            "options": [
                {"value": "co_duration_desc", "label": "Co-overlap (desc)"},
                {"value": "co_duration_asc", "label": "Co-overlap (asc)"},
                {"value": "presence_sum_desc", "label": "Presence duration (desc)"},
            ],
        },
    ],
    supports_pagination=False,
    exposes_scores=False,
)
async def timespan_tag_filter(ctx: Dict[str, Any], request: RecommendationRequest):
    config = request.config or {}
    service_name = str(config.get("service_name") or DEFAULT_SERVICE_NAME)

    duration_selector = _normalize_tag_selector(config.get("presence_tags"))
    co_selector = _normalize_tag_selector(config.get("cooccurrence_tags"))

    _log.debug(
        "timespan_tag_filter requested: service=%s limit=%s offset=%s presence_tags=%s co_tags=%s",
        service_name,
        request.limit,
        request.offset,
        {
            "include": duration_selector.include,
            "exclude": duration_selector.exclude,
            "combination": duration_selector.combination,
            "constraints": list(duration_selector.constraints.keys()),
        },
        {
            "include": co_selector.include,
            "exclude": co_selector.exclude,
            "combination": co_selector.combination,
            "constraints": list(co_selector.constraints.keys()),
        },
    )

    duration_constraints = _extract_duration_constraints(duration_selector)
    if not duration_constraints:
        legacy_tags = _parse_tag_ids(config.get("presence_tags"))
        if legacy_tags:
            legacy_value = _safe_float(config.get("presence_threshold_value"), 0.0)
            legacy_mode = str(config.get("presence_threshold_mode") or "seconds").lower()
            for tag_id in legacy_tags:
                duration_constraints.append(
                    DurationConstraint(
                        tag_id=tag_id,
                        min_value=legacy_value,
                        max_value=None,
                        unit=legacy_mode if legacy_mode in {"seconds", "percent"} else "seconds",
                        require_presence=True,
                    )
                )
        elif duration_selector.include:
            for tag_id in duration_selector.include:
                duration_constraints.append(
                    DurationConstraint(
                        tag_id=tag_id,
                        min_value=0.0,
                        max_value=None,
                        unit="seconds",
                        require_presence=True,
                    )
                )

    co_constraints = _extract_cooccurrence_constraints(co_selector)
    if not co_constraints:
        legacy_co_tags = _parse_tag_ids(config.get("cooccurrence_tags"))
        if legacy_co_tags:
            legacy_value = _safe_float(config.get("cooccurrence_threshold_value"), 0.0)
            legacy_mode = str(config.get("cooccurrence_threshold_mode") or "seconds").lower()
            primary = legacy_co_tags[0]
            co_constraints.append(
                CoOccurrenceConstraint(
                    primary_tag=primary,
                    co_tags=[tag for tag in legacy_co_tags[1:] if tag != primary],
                    min_value=legacy_value,
                    max_value=None,
                    unit=legacy_mode if legacy_mode in {"seconds", "percent"} else "seconds",
                    require_presence=True,
                )
            )

    if duration_constraints:
        duration_debug = [
            {
                "tag": constraint.tag_id,
                "min": constraint.min_value,
                "max": constraint.max_value,
                "unit": constraint.unit,
                "require_presence": constraint.require_presence,
            }
            for constraint in duration_constraints
        ]
        _log.debug("Parsed %d duration constraints: %s", len(duration_constraints), duration_debug)
    else:
        _log.debug("Parsed zero duration constraints from selector")

    if co_constraints:
        co_debug = [
            {
                "primary": constraint.primary_tag,
                "co_tags": constraint.co_tags,
                "min": constraint.min_value,
                "max": constraint.max_value,
                "unit": constraint.unit,
                "require_presence": constraint.require_presence,
            }
            for constraint in co_constraints
        ]
        _log.debug("Parsed %d co-occurrence constraints: %s", len(co_constraints), co_debug)
    else:
        _log.debug("Parsed zero co-occurrence constraints from selector")

    if not duration_constraints and not co_constraints:
        _log.debug("timespan_tag_filter exited: no duration or co-occurrence constraints parsed")
        return []

    ordering = str(config.get("ordering") or "co_duration_desc")

    target_tags: set[int] = set(duration_selector.include)
    for constraint in duration_constraints:
        target_tags.add(constraint.tag_id)
    for constraint in co_constraints:
        target_tags.add(constraint.primary_tag)
        target_tags.update(constraint.co_tags)

    if not target_tags:
        _log.debug("timespan_tag_filter exited: no target tags after parsing constraints")
        return []

    tag_duration_map = collect_tag_durations(service=service_name, tag_ids=target_tags)
    if not tag_duration_map:
        _log.debug(
            "timespan_tag_filter exited: no tag durations found for service=%s tags=%s",
            service_name,
            sorted(target_tags),
        )
        return []

    limit = request.limit or DEFAULT_LIMIT
    offset = request.offset or 0

    raw_candidates: List[Dict[str, Any]] = []
    for scene_id, durations in tag_duration_map.items():
        duration_lookup = {int(tag): float(seconds) for tag, seconds in durations.items()}
        presence_sum = sum(duration_lookup.get(tag, 0.0) for tag in target_tags)
        raw_candidates.append(
            {
                "scene_id": scene_id,
                "duration_lookup": duration_lookup,
                "presence_sum": presence_sum,
            }
        )

    if not raw_candidates:
        return []

    candidate_goal = max(limit * MAX_CANDIDATE_MULTIPLIER, MIN_CANDIDATE_POOL)
    candidate_goal = min(candidate_goal, MAX_CANDIDATE_POOL)
    raw_candidates.sort(key=lambda item: item.get("presence_sum", 0.0), reverse=True)
    trimmed_candidates = raw_candidates[:candidate_goal]

    _log.debug(
        "Collected candidates for %d scenes (requested_limit=%s candidate_goal=%s)",
        len(trimmed_candidates),
        limit,
        candidate_goal,
    )

    scene_id_list = [entry["scene_id"] for entry in trimmed_candidates]
    scene_lookup = fetch_scenes_by_ids(scene_id_list)

    filtered: List[Dict[str, Any]] = []
    for entry in trimmed_candidates:
        scene_id = entry["scene_id"]
        scene_payload = scene_lookup.get(scene_id)
        if not scene_payload:
            _log.debug("Scene %s dropped: payload missing from fetch", scene_id)
            continue

        duration_lookup = entry["duration_lookup"]
        scene_duration = _extract_scene_duration(scene_payload)

        duration_ok, duration_details = _evaluate_duration_constraints(
            duration_constraints,
            duration_selector.combination,
            duration_lookup,
            scene_duration,
        )
        if not duration_ok:
            _log.debug(
                "Scene %s dropped by duration constraints: duration_details=%s scene_duration=%s",
                scene_id,
                duration_details,
                scene_duration,
            )
            continue

        if co_constraints:
            co_ok, co_details, co_score = _evaluate_cooccurrence_constraints(
                co_constraints,
                co_selector.combination,
                scene_id,
                scene_duration,
                service_name=service_name,
            )
            if not co_ok:
                _log.debug(
                    "Scene %s dropped by co-occurrence constraints: co_details=%s scene_duration=%s",
                    scene_id,
                    co_details,
                    scene_duration,
                )
                continue
        else:
            co_details = []
            co_score = entry.get("presence_sum", 0.0)

        entry["scene_payload"] = scene_payload
        entry["scene_duration"] = scene_duration
        entry["duration_details"] = duration_details
        entry["co_details"] = co_details
        entry["co_duration_score"] = co_score
        entry["rating100"] = scene_payload.get("rating100")
        filtered.append(entry)

    if not filtered:
        _log.debug(
            "timespan_tag_filter exited: all %d candidates removed by constraints (service=%s tags=%s)",
            len(trimmed_candidates),
            service_name,
            sorted(target_tags),
        )
        return []

    _sort_candidates(ordering, filtered)

    slice_start = max(offset, 0)
    slice_end = slice_start + (limit if limit else DEFAULT_LIMIT)
    paged = filtered[slice_start:slice_end]

    target_tag_list = sorted(target_tags)
    results: List[Dict[str, Any]] = []
    for entry in paged:
        payload = dict(entry["scene_payload"])
        debug_meta = dict(payload.get("debug_meta") or {})
        debug_meta.update(
            {
                "source": "timespan_tag_filter",
                "service": service_name,
                "target_tags": target_tag_list,
                "raw_tag_durations_s": {str(tag): entry["duration_lookup"].get(tag, 0.0) for tag in target_tag_list},
                "duration_constraints": entry.get("duration_details", []),
                "duration_combination": duration_selector.combination,
                "cooccurrence_constraints": entry.get("co_details", []),
                "cooccurrence_combination": co_selector.combination,
                "scene_duration_s": entry.get("scene_duration"),
                "score_basis": entry.get("co_duration_score", 0.0),
            }
        )
        payload["debug_meta"] = debug_meta
        payload["score"] = entry.get("co_duration_score", entry.get("presence_sum", 0.0))
        results.append(payload)

    _log.debug(
        "timespan_tag_filter returning %d scenes (offset=%s limit=%s ordering=%s)",
        len(results),
        offset,
        limit,
        ordering,
    )

    return results
