from __future__ import annotations

import gzip
import logging
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from stash_ai_server.db.ai_results_store import store_scene_run_async
from stash_ai_server.services.base import RemoteServiceBase
from stash_ai_server.utils.path_mutation import mutate_path_for_backend

from .tag_config import get_tag_configuration, resolve_backend_to_stash_tag_id

_log = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.5
MERGE_GAP_SECONDS = 4.0
FRAME_INTERVAL_FALLBACK = 2.0
LEGACY_SOURCE_LABEL = "legacy_ai_json"


class LegacyTagTimeFrame(BaseModel):
    model_config = ConfigDict(extra="ignore")

    start: float
    end: Optional[float] = None
    confidence: Optional[float] = None


class LegacyModelInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    frame_interval: Optional[float] = None
    threshold: float = 0.5
    version: Optional[float] = None
    ai_model_id: Optional[int] = None
    file_name: Optional[str] = None


class LegacyVideoMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    duration: Optional[float] = None
    models: Dict[str, LegacyModelInfo] = Field(default_factory=dict)


class LegacyAIVideoResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: int = 1
    metadata: LegacyVideoMetadata
    timespans: Dict[str, Dict[str, List[LegacyTagTimeFrame]]] = Field(default_factory=dict)
    scene_path: Optional[str] = None
    json_path: Optional[str] = None

    @classmethod
    async def from_json_file(cls, json_file: Path | str) -> "LegacyAIVideoResult":
        path = Path(json_file)
        data = cls._read_json_text(path)
        instance = cls.model_validate_json(data)
        instance.json_path = str(path)
        return instance

    @classmethod
    async def try_load_from_scene_path(cls, scene_path: str | None) -> Optional["LegacyAIVideoResult"]:
        if not scene_path:
            return None

        mutated = mutate_path_for_backend(scene_path) or scene_path
        base_path = Path(mutated)
        candidates = (
            Path(f"{base_path}.AI.json"),
            Path(f"{base_path}.ai.json"),
        )

        for json_path in candidates:
            if not json_path.is_file():
                continue
            try:
                instance = await cls.from_json_file(json_path)
            except Exception:  # pragma: no cover - defensive logging
                _log.exception("Failed to load legacy AI JSON from %s", json_path)
                continue
            instance.scene_path = scene_path
            return instance

        return None

    async def save_to_db(self, *, scene_id: int, service: RemoteServiceBase) -> bool:
        payload, models_payload, frame_interval, threshold = self._to_modern_payload()
        if not payload.get("timespans"):
            _log.info("Legacy AI result for scene_id=%s contained no qualifying timespans", scene_id)
            return False

        tag_config = get_tag_configuration()

        input_params: Dict[str, Any] = {"source": LEGACY_SOURCE_LABEL}
        if frame_interval is not None:
            input_params["frame_interval"] = frame_interval
        if threshold is not None:
            input_params["threshold"] = threshold
        if self.scene_path:
            input_params["scene_path"] = self.scene_path
        if self.json_path:
            input_params["legacy_json_path"] = self.json_path

        input_params_payload = {k: v for k, v in input_params.items() if v is not None}

        try:
            await store_scene_run_async(
                service=service.name,
                plugin_name=service.plugin_name,
                scene_id=scene_id,
                input_params=input_params_payload or None,
                result_payload=payload,
                requested_models=models_payload,
                resolve_reference=lambda backend_label, category: resolve_backend_to_stash_tag_id(
                    backend_label,
                    tag_config,
                    category,
                ),
            )
        except Exception:  # pragma: no cover - defensive logging
            _log.exception("Failed to persist legacy AI scene run for scene_id=%s", scene_id)
            return False

        _log.info("Imported legacy AI result for scene_id=%s", scene_id)
        return True

    def _to_modern_payload(
        self,
    ) -> tuple[Dict[str, Any], List[Dict[str, Any]], float | None, float | None]:
        metadata = self.metadata or LegacyVideoMetadata()
        legacy_models = metadata.models or {}

        requested_models: List[Dict[str, Any]] = []
        payload_models: List[Dict[str, Any]] = []
        model_intervals: Dict[str, float | None] = {}

        frame_interval: float | None = None
        threshold: float | None = None

        for category, model_info in legacy_models.items():
            interval = self._safe_float(model_info.frame_interval)
            model_intervals[category] = interval
            if interval is not None and frame_interval is None:
                frame_interval = interval

            model_info.threshold = 0.5 # override threshold to 0.5 because for practical purposes thats what it was
            model_threshold = self._safe_float(model_info.threshold)
            if model_threshold is not None and threshold is None:
                threshold = model_threshold

            identifier = self._safe_int(model_info.ai_model_id)
            version = self._safe_float(model_info.version)
            model_name = (model_info.file_name or str(category)).strip() or str(category)

            params: Dict[str, Any] = {}
            if interval is not None:
                params["frame_interval"] = interval
            if model_threshold is not None:
                params["threshold"] = model_threshold

            requested_entry: Dict[str, Any] = {
                "name": model_name,
                "type": "legacy",
                "categories": [category],
            }
            if identifier is not None:
                requested_entry["identifier"] = identifier
            if version is not None:
                requested_entry["version"] = version
            if params:
                requested_entry["input_params"] = params

            payload_entry = {k: v for k, v in requested_entry.items() if k != "input_params"}

            requested_models.append(requested_entry)
            payload_models.append(payload_entry)

        timespans_payload: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        video_duration = self._safe_float(metadata.duration)

        for category, tag_map in (self.timespans or {}).items():
            processed = self._process_category_timespans(
                tag_map,
                interval=model_intervals.get(category, frame_interval),
                duration=video_duration,
            )
            if processed:
                timespans_payload[category] = processed

        payload: Dict[str, Any] = {
            "schema_version": 3,
            "models": payload_models,
            "timespans": timespans_payload,
        }

        if video_duration is not None:
            payload["duration"] = video_duration

        effective_interval = frame_interval
        if effective_interval is None and timespans_payload:
            effective_interval = FRAME_INTERVAL_FALLBACK

        if effective_interval is not None:
            payload["frame_interval"] = effective_interval

        return payload, requested_models, effective_interval, threshold

    def _process_category_timespans(
        self,
        tag_map: Mapping[str, Sequence[LegacyTagTimeFrame]],
        *,
        interval: float | None,
        duration: float | None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        processed: Dict[str, List[Dict[str, Any]]] = {}
        for raw_label, frames in tag_map.items():
            merged = self._merge_frames(frames, interval=interval, duration=duration)
            if not merged:
                continue
            label_key = str(raw_label).strip() or str(raw_label)
            processed[label_key] = [
                {
                    "start": start,
                    "end": end,
                    "confidence": confidence,
                }
                for start, end, confidence in merged
            ]
        return processed

    @staticmethod
    def _read_json_text(path: Path) -> str:
        if path.suffix.lower().endswith(".gz"):
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                return handle.read()
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _merge_frames(
        self,
        frames: Sequence[LegacyTagTimeFrame],
        *,
        interval: float | None,
        duration: float | None,
    ) -> List[tuple[float, float, float]]:
        effective_interval = interval if interval and interval > 0 else FRAME_INTERVAL_FALLBACK
        filtered: List[tuple[float, float, float]] = []

        for frame in frames:
            start = self._safe_float(frame.start)
            confidence = self._safe_float(frame.confidence)
            if start is None or confidence is None or confidence < CONFIDENCE_THRESHOLD:
                continue

            end = self._safe_float(frame.end)
            if end is None or end <= start:
                end = start + effective_interval

            if duration is not None:
                end = min(end, duration)

            filtered.append((start, end, confidence))

        if not filtered:
            return []

        filtered.sort(key=lambda item: item[0])

        merged: List[tuple[float, float, float]] = []
        current_start, current_end, confidences = filtered[0][0], filtered[0][1], [filtered[0][2]]

        for start, end, confidence in filtered[1:]:
            if start <= current_end + MERGE_GAP_SECONDS:
                current_end = max(current_end, end)
                confidences.append(confidence)
            else:
                merged.append((current_start, current_end, max(confidences)))
                current_start, current_end, confidences = start, end, [confidence]

        merged.append((current_start, current_end, max(confidences)))
        return merged
