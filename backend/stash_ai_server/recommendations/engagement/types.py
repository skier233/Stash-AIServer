"""Data types for the engagement scoring subsystem."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SignalValue:
    """A single engagement signal measurement."""

    name: str
    value: float  # 0.0–1.0 normalized
    raw: Any  # original value before normalization (for debug)
    available: bool  # whether data was present
    reliability: float  # how strongly this signal type reflects true engagement (0.0–1.0)
    source: str  # "stash_db", "ai_server", "merged"
    weight: float = 1.0  # caller-supplied importance weight (default 1.0)


@dataclass
class EngagementResult:
    """Engagement score for a single scene (or image)."""

    entity_id: int
    entity_type: str  # "scene" or "image"
    score: float  # 0.0–1.0 noisy-OR cumulative engagement score
    confidence: float  # 0.0–1.0 fraction of total possible signal weight that was available
    signal_count: int  # how many signals contributed to the score
    total_possible: int  # total number of signals defined
    signals: dict[str, SignalValue] = field(default_factory=dict)


@dataclass
class SegmentScore:
    """Engagement score for a time segment within a scene."""

    start_s: float
    end_s: float
    score: float  # 0.0–1.0 rewatch density within this segment
    watch_count: int  # how many watch segments overlapped this region
    total_watch_s: float  # total seconds of watch coverage in this segment


@dataclass
class WeightedEmbedding:
    """An embedding vector weighted by the engagement score of its time region."""

    embedding: Any  # numpy array or list
    weight: float  # engagement score for this segment
    start_s: float
    end_s: float
    entity_id: int
    entity_type: str
    embedding_type: str  # e.g. "visual_dinov3_section_3"


@dataclass
class SceneEngagementDetail:
    """Full engagement detail for a scene, including segment-level data."""

    result: EngagementResult
    segments: list[SegmentScore] = field(default_factory=list)
    scene_duration: float | None = None  # duration from metadata
    watch_sessions: int = 0  # number of distinct watch sessions
    total_watch_s: float = 0.0  # total seconds watched across all sessions
