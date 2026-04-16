"""Unit tests for face_processor.py — track builder, embedding dedup, cluster matching.

These tests exercise the pure-logic functions without needing a running database.
Database-touching functions are mocked.

The skier_aitagging plugin lives in an external registry repo and may not be
present in every checkout.  All tests in this module are skipped when the
plugin is unavailable.
"""
from __future__ import annotations

import math
import sys
import pathlib
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# Ensure the plugins directory is importable
_plugins_dir = pathlib.Path(__file__).resolve().parents[1] / "plugins"
if str(_plugins_dir) not in sys.path:
    sys.path.insert(0, str(_plugins_dir))

# Skip the entire module when the plugin is not installed / checked-out
pytest.importorskip("skier_aitagging", reason="skier_aitagging plugin not available")

from plugins.skier_aitagging.face_processor import (  # noqa: E402
    _iou,
    _l2_normalise,
    build_tracks,
    match_to_cluster,
    select_representative_embeddings,
    has_embedding_capability,
)
from plugins.skier_aitagging.models import (  # noqa: E402
    Detection,
    FrameEmbedding,
    ParsedFrameData,
    AIModelInfo,
    TrackCandidate,
)


# ---------------------------------------------------------------------------
# IoU
# ---------------------------------------------------------------------------

class TestIoU:
    def test_identical_boxes(self):
        assert _iou([0, 0, 1, 1], [0, 0, 1, 1]) == pytest.approx(1.0)

    def test_no_overlap(self):
        assert _iou([0, 0, 0.5, 0.5], [0.6, 0.6, 1, 1]) == 0.0

    def test_partial_overlap(self):
        # Two boxes: [0,0,0.5,0.5] and [0.25,0.25,0.75,0.75]
        # Intersection: [0.25,0.25,0.5,0.5] → area = 0.0625
        # Area A = 0.25, Area B = 0.25
        # Union = 0.25 + 0.25 - 0.0625 = 0.4375
        iou = _iou([0, 0, 0.5, 0.5], [0.25, 0.25, 0.75, 0.75])
        assert iou == pytest.approx(0.0625 / 0.4375, abs=1e-6)

    def test_one_inside_other(self):
        # Small box fully inside big box
        iou = _iou([0.1, 0.1, 0.2, 0.2], [0, 0, 1, 1])
        # Intersection = small area = 0.01
        # Union = 1.0 + 0.01 - 0.01 = 1.0
        assert iou == pytest.approx(0.01, abs=1e-6)

    def test_zero_area_box(self):
        assert _iou([0.5, 0.5, 0.5, 0.5], [0, 0, 1, 1]) == 0.0

    def test_touching_edges(self):
        # Boxes share an edge but no interior overlap
        assert _iou([0, 0, 0.5, 0.5], [0.5, 0, 1, 0.5]) == 0.0


# ---------------------------------------------------------------------------
# L2 normalisation
# ---------------------------------------------------------------------------

class TestL2Normalise:
    def test_unit_vector(self):
        vec = [1.0, 0.0, 0.0]
        normed, norm = _l2_normalise(vec)
        assert norm == pytest.approx(1.0)
        assert np.allclose(normed, [1, 0, 0])

    def test_regular_vector(self):
        vec = [3.0, 4.0]
        normed, norm = _l2_normalise(vec)
        assert norm == pytest.approx(5.0)
        assert np.allclose(normed, [0.6, 0.8])

    def test_zero_vector(self):
        vec = [0.0, 0.0, 0.0]
        normed, norm = _l2_normalise(vec)
        assert norm == 0.0
        assert np.allclose(normed, [0, 0, 0])

    def test_numpy_input(self):
        arr = np.array([1.0, 1.0], dtype=np.float64)
        normed, norm = _l2_normalise(arr)
        assert norm == pytest.approx(math.sqrt(2.0))
        expected = 1.0 / math.sqrt(2.0)
        assert np.allclose(normed, [expected, expected])


# ---------------------------------------------------------------------------
# Track builder
# ---------------------------------------------------------------------------

def _make_frame(
    frame_index: float,
    detections: list[tuple[list[float], float, str]] | None = None,
    regions: dict | None = None,
) -> ParsedFrameData:
    """Helper: make a ParsedFrameData with face detections."""
    dets: dict = {}
    if detections:
        dets["face_detections"] = [
            Detection(bbox=box, score=score, detector=det)
            for box, score, det in detections
        ]
    return ParsedFrameData(
        frame_index=frame_index,
        detections=dets,
        regions=regions or {},
    )


class TestBuildTracks:
    def test_single_detection_single_frame(self):
        frames = [_make_frame(0, [([0.1, 0.1, 0.3, 0.3], 0.95, "yolov8")])]
        tracks = build_tracks(frames, frame_interval=2.0)
        assert len(tracks) == 1
        t = tracks[0]
        assert t.label == "face"
        assert t.best_bbox == [0.1, 0.1, 0.3, 0.3]
        assert t.best_score == pytest.approx(0.95)
        assert t.start_s == pytest.approx(0.0)
        assert t.end_s == pytest.approx(0.0)

    def test_single_face_across_frames(self):
        """A face at roughly the same position across 3 frames → one track."""
        # frame_index values are timestamps in seconds (matching AI model output)
        frames = [
            _make_frame(0.0, [([0.1, 0.1, 0.3, 0.3], 0.9, "yolo")]),
            _make_frame(2.0, [([0.11, 0.11, 0.31, 0.31], 0.92, "yolo")]),
            _make_frame(4.0, [([0.12, 0.12, 0.32, 0.32], 0.88, "yolo")]),
        ]
        tracks = build_tracks(frames, frame_interval=2.0)
        assert len(tracks) == 1
        t = tracks[0]
        assert t.best_score == pytest.approx(0.92)
        assert t.start_s == pytest.approx(0.0)
        assert t.end_s == pytest.approx(4.0)  # frame at 4.0s

    def test_two_faces_two_tracks(self):
        """Two non-overlapping faces in same frame → two tracks."""
        frames = [
            _make_frame(0, [
                ([0.0, 0.0, 0.2, 0.2], 0.9, "yolo"),
                ([0.7, 0.7, 0.9, 0.9], 0.8, "yolo"),
            ]),
        ]
        tracks = build_tracks(frames, frame_interval=1.0)
        assert len(tracks) == 2

    def test_track_gap_closes_track(self):
        """Face disappears for > max_gap frames → track is closed, then new track starts."""
        frames = [
            _make_frame(0, [([0.1, 0.1, 0.3, 0.3], 0.9, "yolo")]),
            _make_frame(1, []),  # empty
            _make_frame(2, []),
            _make_frame(3, []),
            _make_frame(4, []),  # gap of 4 frames (> default 3)
            _make_frame(5, [([0.1, 0.1, 0.3, 0.3], 0.85, "yolo")]),
        ]
        tracks = build_tracks(frames, frame_interval=1.0, max_gap_frames=3)
        assert len(tracks) == 2

    def test_empty_frames_no_tracks(self):
        frames = [_make_frame(0, []), _make_frame(1, [])]
        tracks = build_tracks(frames, frame_interval=1.0)
        assert len(tracks) == 0

    def test_no_face_category(self):
        """Frames with detections in a different category → no face tracks."""
        frame = ParsedFrameData(
            frame_index=0,
            detections={"person_detections": [Detection(bbox=[0, 0, 1, 1], score=0.9, detector="yolo")]},
            regions={},
        )
        tracks = build_tracks([frame], frame_interval=1.0)
        assert len(tracks) == 0


# ---------------------------------------------------------------------------
# Embedding deduplication
# ---------------------------------------------------------------------------

def _make_embedding(
    dim: int = 512,
    score: float = 0.9,
    norm: float = 10.0,
    timestamp_s: float = 0.0,
    seed: int | None = None,
) -> FrameEmbedding:
    rng = np.random.RandomState(seed)
    vec = rng.randn(dim).astype(np.float32)
    vec = vec / np.linalg.norm(vec)  # normalise so cosine sims are meaningful
    return FrameEmbedding(
        vector=vec.tolist(),
        norm=norm,
        score=score,
        bbox=[0.1, 0.1, 0.3, 0.3],
        timestamp_s=timestamp_s,
        embedder="arcface",
    )


class TestSelectRepresentativeEmbeddings:
    def test_empty_input(self):
        assert select_representative_embeddings([]) == []

    def test_single_embedding(self):
        embs = [_make_embedding(seed=42)]
        result = select_representative_embeddings(embs, max_count=10)
        assert len(result) == 1

    def test_under_max_count(self):
        embs = [_make_embedding(seed=i) for i in range(5)]
        result = select_representative_embeddings(embs, max_count=10)
        assert len(result) == 5

    def test_dedup_identical_embeddings(self):
        """Identical embeddings should be deduplicated down to 1."""
        base = _make_embedding(seed=42)
        embs = [
            FrameEmbedding(
                vector=base.vector, norm=10.0, score=0.9,
                bbox=[0.1, 0.1, 0.3, 0.3], timestamp_s=float(i),
                embedder="arcface",
            )
            for i in range(10)
        ]
        result = select_representative_embeddings(embs, max_count=10, dedup_threshold=0.85)
        assert len(result) == 1

    def test_diverse_embeddings_retained(self):
        """Very different embeddings should all be kept (up to max_count)."""
        embs = [_make_embedding(seed=i * 1000 + 7) for i in range(8)]
        result = select_representative_embeddings(embs, max_count=10, dedup_threshold=0.99)
        # With random seeds, they should be diverse enough
        assert len(result) >= 5

    def test_max_count_enforced(self):
        embs = [_make_embedding(seed=i * 1000) for i in range(20)]
        result = select_representative_embeddings(embs, max_count=5)
        assert len(result) <= 5


# ---------------------------------------------------------------------------
# Cluster matching
# ---------------------------------------------------------------------------

class TestMatchToCluster:
    @patch("plugins.skier_aitagging.face_processor.find_nearest_cluster")
    def test_auto_match(self, mock_find):
        mock_find.return_value = [(1, 0.70)]
        vec = np.ones(512, dtype=np.float32) / np.sqrt(512)
        cluster_id, sim, match_type = match_to_cluster(vec, auto_threshold=0.55)
        assert cluster_id == 1
        assert sim == pytest.approx(0.70)
        assert match_type == "auto"

    @patch("plugins.skier_aitagging.face_processor.find_nearest_cluster")
    def test_review_match(self, mock_find):
        mock_find.return_value = [(2, 0.45)]
        vec = np.ones(512, dtype=np.float32) / np.sqrt(512)
        cluster_id, sim, match_type = match_to_cluster(
            vec, auto_threshold=0.55, review_threshold=0.35,
        )
        assert cluster_id == 2
        assert sim == pytest.approx(0.45)
        assert match_type == "review"

    @patch("plugins.skier_aitagging.face_processor.find_nearest_cluster")
    def test_new_cluster_low_sim(self, mock_find):
        mock_find.return_value = [(3, 0.20)]
        vec = np.ones(512, dtype=np.float32) / np.sqrt(512)
        cluster_id, sim, match_type = match_to_cluster(
            vec, auto_threshold=0.55, review_threshold=0.35,
        )
        assert cluster_id is None
        assert match_type == "new"

    @patch("plugins.skier_aitagging.face_processor.find_nearest_cluster")
    def test_new_cluster_no_results(self, mock_find):
        mock_find.return_value = []
        vec = np.ones(512, dtype=np.float32) / np.sqrt(512)
        cluster_id, sim, match_type = match_to_cluster(vec)
        assert cluster_id is None
        assert match_type == "new"
        assert sim == 0.0


# ---------------------------------------------------------------------------
# has_embedding_capability
# ---------------------------------------------------------------------------

class TestHasEmbeddingCapability:
    def test_no_models(self):
        assert has_embedding_capability([]) is False

    def test_tagging_only(self):
        models = [AIModelInfo(name="tagger", identifier=1, version=1.0, categories=["actions"], type="tagging", capabilities=["tagging"])]
        assert has_embedding_capability(models) is False

    def test_detection_model(self):
        models = [AIModelInfo(name="yolo", identifier=2, version=1.0, categories=["face_detections"], type="detection", capabilities=["detection"])]
        assert has_embedding_capability(models) is True

    def test_embedding_model(self):
        models = [AIModelInfo(name="arcface", identifier=3, version=1.0, categories=["face_embeddings"], type="embedding", capabilities=["embedding"])]
        assert has_embedding_capability(models) is True

    def test_mixed_models(self):
        models = [
            AIModelInfo(name="tagger", identifier=1, version=1.0, categories=["actions"], type="tagging", capabilities=["tagging"]),
            AIModelInfo(name="arcface", identifier=3, version=1.0, categories=["face_embeddings"], type="embedding", capabilities=["embedding"]),
        ]
        assert has_embedding_capability(models) is True

    def test_dict_models(self):
        models = [{"name": "arcface", "capabilities": ["detection", "embedding"]}]
        assert has_embedding_capability(models) is True

    def test_dict_tagging_only(self):
        models = [{"name": "tagger", "capabilities": ["tagging"]}]
        assert has_embedding_capability(models) is False


# ---------------------------------------------------------------------------
# Reprocessing skip-logic
# ---------------------------------------------------------------------------

class TestClassifyModelCategories:
    def test_mixed_models(self):
        from plugins.skier_aitagging.reprocessing import classify_model_categories

        models = [
            AIModelInfo(name="tagger", identifier=1, version=1.0, categories=["bodyparts", "actions"], type="tagging", capabilities=["tagging"]),
            AIModelInfo(name="yolo", identifier=2, version=1.0, categories=["face_detections"], type="detection", capabilities=["detection"]),
            AIModelInfo(name="arcface", identifier=3, version=1.0, categories=["face_embeddings"], type="embedding", capabilities=["embedding"]),
        ]
        tag_cats, emb_cats = classify_model_categories(models)
        assert tag_cats == {"bodyparts", "actions"}
        assert emb_cats == {"face_detections", "face_embeddings"}

    def test_all_tagging(self):
        from plugins.skier_aitagging.reprocessing import classify_model_categories

        models = [
            AIModelInfo(name="tagger", identifier=1, version=1.0, categories=["bodyparts", "actions"], type="tagging", capabilities=["tagging"]),
        ]
        tag_cats, emb_cats = classify_model_categories(models)
        assert tag_cats == {"bodyparts", "actions"}
        assert emb_cats == set()

    def test_empty_models(self):
        from plugins.skier_aitagging.reprocessing import classify_model_categories
        tag_cats, emb_cats = classify_model_categories([])
        assert tag_cats == set()
        assert emb_cats == set()
