from __future__ import annotations
import logging
from typing import Iterable, Sequence

from .models import AIModelInfo
from stash_ai_server.db.ai_results_store import StoredModelSummary, StoredSceneRun


_log = logging.getLogger(__name__)

def determine_model_plan(
    *,
    current_models: Sequence[AIModelInfo],
    previous_models: Sequence[StoredModelSummary],
    current_frame_interval: float,
    current_threshold: float,
) -> tuple[set[str], bool]:
    """Return (categories_to_skip, will_reprocess)."""

    categories_to_skip: set[str] = set()
    will_reprocess = True

    # Get dict of category to previous model (name, version, identifier)
    previous_by_categories: dict[str, tuple[str, int, float, float, float]] = {}
    for prev_model in previous_models:
        _log.debug("Evaluating historical model: %s", prev_model)
        if not prev_model.categories:
            continue
        prev_model_id = prev_model.model_id if prev_model.model_id is not None else -1
        prev_version = prev_model.version if prev_model.version is not None else 0.0
        prev_frame_interval = prev_model.frame_interval if prev_model.frame_interval is not None else 2.0
        prev_threshold = prev_model.threshold if prev_model.threshold is not None else 0.5
        for category in prev_model.categories:
            existing = previous_by_categories.get(category)
            candidate = (prev_model.model_name, prev_model_id, prev_version, prev_frame_interval, prev_threshold)
            if existing is None or not _should_skip_category(existing, candidate):
                previous_by_categories[category] = candidate

    if not previous_by_categories:
        return set(), True
    
    current_categories: dict[str, tuple[str, int, float]] = {}
    for model in current_models:
        if not model.categories:
            continue
        for category in model.categories:
            current_categories[category] = (
                model.name,
                model.identifier,
                model.version,
            )

    for category, (curr_name, curr_id, curr_version) in current_categories.items():
        prev_entry = previous_by_categories.get(category)
        _log.debug("Evaluating category '%s'", category)
        if (_should_skip_category(prev_entry, (curr_name, curr_id, curr_version, current_frame_interval, current_threshold))):
            categories_to_skip.add(category)

    if len(categories_to_skip) == len(current_categories):
        will_reprocess = False

    return categories_to_skip, will_reprocess


#TODO: implement and move to another file
def derive_scene_annotations(
    *,
    scene_id: int,
    service_name: str,
    categories_processed: Iterable[str],
) -> dict[str, object]:
    """Placeholder for downstream tag/marker derivation."""
    return {
        "scene_id": scene_id,
        "service": service_name,
        "processed_categories": list(categories_processed),
        "summary": "Annotation derivation not yet implemented",
    }


def _should_skip_category(
    prev_category: tuple[str, int, float, float, float] | None,
    current_category: tuple[str, int, float, float, float]
) -> bool:
    _log.debug("Comparing previous category %s with current category %s", prev_category, current_category)
    if prev_category is None:
        _log.debug("No previous category found; cannot skip")
        return False
    prev_name, prev_id, prev_version, prev_frame_interval, prev_threshold = prev_category
    curr_name, curr_id, curr_version, current_frame_interval, current_threshold = current_category
    if current_threshold != prev_threshold:
        _log.debug("Thresholds differ (prev: %s, curr: %s); cannot skip", prev_threshold, current_threshold)
        return False

    if current_frame_interval != prev_frame_interval and prev_frame_interval % current_frame_interval != 0:
        _log.debug("Frame intervals differ (prev: %s, curr: %s); cannot skip", prev_frame_interval, current_frame_interval)
        return False

    if curr_version < prev_version:
        _log.debug("Current version %s is less than previous version %s; skipping", curr_version, prev_version)
        return True
    elif curr_version == prev_version:
        if curr_id == prev_id and curr_name == prev_name:
            _log.debug("Model unchanged (name: %s, id: %s); skipping", curr_name, curr_id)
            return True
        elif curr_id >= prev_id:
            # maybe make this configurable
            _log.debug("Model id %s is greater than or equal to previous id %s; skipping", curr_id, prev_id)
            return True
        _log.debug("Model changed (name/id); cannot skip")
        return False
    else:
        _log.debug("Current version %s is greater than previous version %s; cannot skip", curr_version, prev_version)
        return False