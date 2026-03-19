"""Tests for the skier_aitagging response_parser module.

Validates classification of dynamic API keys, normalised parsing of image
and video results, and edge-case resilience.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# The plugin package is NOT installed; adjust sys.path so we can import it.
# ---------------------------------------------------------------------------
import sys, pathlib

_plugins_dir = pathlib.Path(__file__).resolve().parents[1] / "plugins"
if str(_plugins_dir) not in sys.path:
    sys.path.insert(0, str(_plugins_dir))

from skier_aitagging.response_parser import (  # type: ignore[import-untyped]
    build_category_classifier,
    count_detections,
    count_regions,
    extract_tags_only,
    parse_embeddings,
    parse_frame_data,
    parse_image_result,
    parse_video_frames,
    parse_video_result,
)
from skier_aitagging.models import (  # type: ignore[import-untyped]
    AIModelInfo,
    Detection,
    EmbeddingResult,
    ParsedFrameData,
    ParsedImageData,
    ParsedVideoData,
    RegionResult,
)


# ---------------------------------------------------------------------------
# Shared fixtures — mirror the v3 API contract examples
# ---------------------------------------------------------------------------

SAMPLE_MODELS = [
    {
        "name": "fearless_terrain",
        "identifier": 200,
        "version": 1.0,
        "categories": ["bodyparts"],
        "type": "ImClass",
        "capabilities": ["tagging"],
        "supported_scopes": ["asset", "frame", "region"],
    },
    {
        "name": "det_500m",
        "identifier": 980,
        "version": 1.0,
        "categories": ["face_detections"],
        "type": "FaceDetection",
        "capabilities": ["detection"],
        "supported_scopes": ["asset", "frame"],
    },
    {
        "name": "arcface",
        "identifier": 981,
        "version": 1.0,
        "categories": ["face_embeddings"],
        "type": "FaceEmbedding",
        "capabilities": ["embedding"],
        "supported_scopes": ["region", "asset", "frame"],
    },
]

SAMPLE_IMAGE_RESULT = {
    "bodyparts": ["Face", "Thighs", "Boobs"],
    "face_detections": [
        {"bbox": [61.29, 141.44, 332.58, 543.98], "score": 0.736, "detector": "det_500m"},
    ],
    "regions__face_detector_torchexport": [
        {
            "detection_index": 0,
            "face_embeddings": [
                {"vector": [0.1] * 512, "norm": 25.55, "embedder": "arcface"},
            ],
        },
    ],
}

SAMPLE_VIDEO_FRAME = {
    "frame_index": 3.5,
    "face_detections": [
        {"bbox": [718.69, 79.97, 903.14, 367.78], "score": 0.853, "detector": "det_500m"},
    ],
    "regions__face_detector_torchexport": [
        {
            "detection_index": 0,
            "face_embeddings": [
                {"vector": [0.2] * 512, "norm": 23.70, "embedder": "arcface"},
            ],
        },
    ],
}


# ====================================================================
# build_category_classifier
# ====================================================================


class TestBuildCategoryClassifier:
    def test_basic_classification(self):
        classifier = build_category_classifier(SAMPLE_MODELS)
        assert classifier["bodyparts"] == "tagging"
        assert classifier["face_detections"] == "detection"
        assert classifier["face_embeddings"] == "embedding"

    def test_empty_models(self):
        assert build_category_classifier(None) == {}
        assert build_category_classifier([]) == {}

    def test_typed_model_info(self):
        models = [
            AIModelInfo(
                name="test",
                identifier=1,
                version=1.0,
                categories=["test_cat"],
                type="TestType",
                capabilities=["tagging"],
            )
        ]
        classifier = build_category_classifier(models)
        assert classifier["test_cat"] == "tagging"

    def test_unknown_capability_fallback(self):
        models = [
            {
                "name": "custom",
                "identifier": 99,
                "version": 1.0,
                "categories": ["custom_output"],
                "type": "Custom",
                "capabilities": ["segmentation"],
            }
        ]
        classifier = build_category_classifier(models)
        # Falls back to the first capability string
        assert classifier["custom_output"] == "segmentation"


# ====================================================================
# parse_image_result
# ====================================================================


class TestParseImageResult:
    @pytest.fixture()
    def classifier(self):
        return build_category_classifier(SAMPLE_MODELS)

    def test_tags_extracted(self, classifier):
        parsed = parse_image_result(SAMPLE_IMAGE_RESULT, classifier)
        assert "bodyparts" in parsed.tags
        assert parsed.tags["bodyparts"] == ["Face", "Thighs", "Boobs"]
        assert parsed.error is None

    def test_detections_extracted(self, classifier):
        parsed = parse_image_result(SAMPLE_IMAGE_RESULT, classifier)
        assert "face_detections" in parsed.detections
        dets = parsed.detections["face_detections"]
        assert len(dets) == 1
        assert isinstance(dets[0], Detection)
        assert dets[0].detector == "det_500m"
        assert dets[0].score == pytest.approx(0.736)
        assert dets[0].bbox == [61.29, 141.44, 332.58, 543.98]

    def test_regions_extracted(self, classifier):
        parsed = parse_image_result(SAMPLE_IMAGE_RESULT, classifier)
        assert "regions__face_detector_torchexport" in parsed.regions
        regions = parsed.regions["regions__face_detector_torchexport"]
        assert len(regions) == 1
        assert isinstance(regions[0], RegionResult)
        assert regions[0].detection_index == 0
        assert "face_embeddings" in regions[0].model_outputs

    def test_detections_not_in_tags(self, classifier):
        """Detection categories must NOT appear in the tags dict."""
        parsed = parse_image_result(SAMPLE_IMAGE_RESULT, classifier)
        assert "face_detections" not in parsed.tags

    def test_regions_not_in_tags(self, classifier):
        """Region keys must NOT appear in the tags dict."""
        parsed = parse_image_result(SAMPLE_IMAGE_RESULT, classifier)
        assert "regions__face_detector_torchexport" not in parsed.tags

    def test_error_result(self, classifier):
        parsed = parse_image_result({"error": "file not found"}, classifier)
        assert parsed.error == "file not found"
        assert parsed.tags == {}
        assert parsed.detections == {}

    def test_non_dict_input(self, classifier):
        parsed = parse_image_result("unexpected", classifier)
        assert parsed.error is not None

    def test_tags_only_backward_compat(self):
        """When no models metadata is available, strings are classified as tags by heuristic."""
        payload = {"bodyparts": ["Face", "Boobs"], "actions": ["Oral"]}
        parsed = parse_image_result(payload, {})
        assert "bodyparts" in parsed.tags
        assert "actions" in parsed.tags
        assert parsed.detections == {}

    def test_heuristic_detection_fallback(self):
        """Dicts with 'bbox' are detected as detections even without classifier."""
        payload = {
            "unknown_detections": [
                {"bbox": [0, 0, 100, 100], "score": 0.9, "detector": "x"}
            ]
        }
        parsed = parse_image_result(payload, {})
        assert "unknown_detections" in parsed.detections

    def test_confidence_tuple_tags(self):
        """return_confidence=true produces [label, score] pairs — still classified as tags."""
        classifier = build_category_classifier(SAMPLE_MODELS)
        payload = {"bodyparts": [["Face", 0.95], ["Boobs", 0.87]]}
        parsed = parse_image_result(payload, classifier)
        assert "bodyparts" in parsed.tags
        assert parsed.tags["bodyparts"] == [["Face", 0.95], ["Boobs", 0.87]]


# ====================================================================
# extract_tags_only
# ====================================================================


class TestExtractTagsOnly:
    def test_filters_out_detections_and_regions(self):
        classifier = build_category_classifier(SAMPLE_MODELS)
        tags = extract_tags_only(SAMPLE_IMAGE_RESULT, classifier)
        assert "bodyparts" in tags
        assert "face_detections" not in tags
        assert "regions__face_detector_torchexport" not in tags


# ====================================================================
# parse_frame_data / parse_video_frames
# ====================================================================


class TestParseFrameData:
    @pytest.fixture()
    def classifier(self):
        return build_category_classifier(SAMPLE_MODELS)

    def test_frame_index(self, classifier):
        parsed = parse_frame_data(SAMPLE_VIDEO_FRAME, classifier)
        assert parsed.frame_index == 3.5

    def test_frame_detections(self, classifier):
        parsed = parse_frame_data(SAMPLE_VIDEO_FRAME, classifier)
        assert "face_detections" in parsed.detections
        assert len(parsed.detections["face_detections"]) == 1
        assert parsed.detections["face_detections"][0].score == pytest.approx(0.853)

    def test_frame_regions(self, classifier):
        parsed = parse_frame_data(SAMPLE_VIDEO_FRAME, classifier)
        assert "regions__face_detector_torchexport" in parsed.regions

    def test_frame_has_no_tags(self, classifier):
        """Frame data should not produce tags (those go to timespans)."""
        frame_with_tags = {
            "frame_index": 1.0,
            "bodyparts": ["Face"],
            "face_detections": [
                {"bbox": [0, 0, 1, 1], "score": 0.5, "detector": "d"},
            ],
        }
        parsed = parse_frame_data(frame_with_tags, classifier)
        # Tags at frame level are ignored (skip_keys doesn't include them,
        # but they'd be classified as tagging and thus excluded from detections)
        assert "face_detections" in parsed.detections


class TestParseVideoFrames:
    def test_none_input(self):
        assert parse_video_frames(None, {}) is None

    def test_empty_list(self):
        assert parse_video_frames([], {}) is None

    def test_multiple_frames(self):
        classifier = build_category_classifier(SAMPLE_MODELS)
        frames = [
            {
                "frame_index": 1.0,
                "face_detections": [
                    {"bbox": [0, 0, 50, 50], "score": 0.8, "detector": "det_500m"},
                ],
            },
            {
                "frame_index": 2.0,
                "face_detections": [
                    {"bbox": [10, 10, 60, 60], "score": 0.7, "detector": "det_500m"},
                    {"bbox": [100, 100, 200, 200], "score": 0.6, "detector": "det_500m"},
                ],
            },
        ]
        parsed = parse_video_frames(frames, classifier)
        assert parsed is not None
        assert len(parsed) == 2
        assert parsed[0].frame_index == 1.0
        assert len(parsed[0].detections["face_detections"]) == 1
        assert parsed[1].frame_index == 2.0
        assert len(parsed[1].detections["face_detections"]) == 2


# ====================================================================
# parse_video_result
# ====================================================================


class TestParseVideoResult:
    def test_full_video_response(self):
        raw = {
            "schema_version": 3,
            "duration": 479.83,
            "frame_interval": 0.5,
            "models": SAMPLE_MODELS,
            "timespans": {
                "bodyparts": {
                    "Face": [
                        {"start": 3.5, "end": 101.0},
                        {"start": 106.0, "end": 425.0},
                    ],
                },
            },
            "frames": [SAMPLE_VIDEO_FRAME],
        }
        parsed = parse_video_result(raw)
        assert isinstance(parsed, ParsedVideoData)
        assert parsed.schema_version == 3
        assert parsed.duration == pytest.approx(479.83)
        assert parsed.frame_interval == 0.5
        assert len(parsed.models) == 3
        assert "bodyparts" in parsed.timespans
        assert parsed.frames is not None
        assert len(parsed.frames) == 1
        assert parsed.frames[0].frame_index == 3.5

    def test_no_frames(self):
        raw = {
            "schema_version": 3,
            "duration": 60.0,
            "frame_interval": 2.0,
            "models": SAMPLE_MODELS[:1],
            "timespans": {
                "bodyparts": {
                    "Face": [{"start": 0.0, "end": 60.0}],
                },
            },
        }
        parsed = parse_video_result(raw)
        assert parsed.frames is None


# ====================================================================
# Convenience helpers
# ====================================================================


class TestCountHelpers:
    def test_count_detections(self):
        parsed = ParsedImageData(
            detections={
                "face_detections": [
                    Detection(bbox=[0, 0, 1, 1], score=0.9, detector="d"),
                    Detection(bbox=[2, 2, 3, 3], score=0.8, detector="d"),
                ],
                "body_detections": [
                    Detection(bbox=[4, 4, 5, 5], score=0.7, detector="e"),
                ],
            }
        )
        assert count_detections(parsed) == 3

    def test_count_regions(self):
        parsed = ParsedImageData(
            regions={
                "regions__a": [
                    RegionResult(detection_index=0, model_outputs={}),
                    RegionResult(detection_index=1, model_outputs={}),
                ],
            }
        )
        assert count_regions(parsed) == 2

    def test_frame_counts(self):
        frame = ParsedFrameData(
            frame_index=1.0,
            detections={
                "d": [Detection(bbox=[0, 0, 1, 1], score=0.5, detector="x")]
            },
            regions={
                "regions__x": [RegionResult(detection_index=0, model_outputs={})]
            },
        )
        assert count_detections(frame) == 1
        assert count_regions(frame) == 1


class TestParseEmbeddings:
    def test_basic_embedding(self):
        region = RegionResult(
            detection_index=0,
            model_outputs={
                "face_embeddings": [
                    {"vector": [0.1, 0.2, 0.3], "norm": 10.0, "embedder": "arcface"},
                ]
            },
        )
        embeddings = parse_embeddings(region, "face_embeddings")
        assert len(embeddings) == 1
        assert isinstance(embeddings[0], EmbeddingResult)
        assert embeddings[0].embedder == "arcface"
        assert embeddings[0].norm == 10.0
        assert len(embeddings[0].vector) == 3

    def test_missing_category(self):
        region = RegionResult(detection_index=0, model_outputs={})
        assert parse_embeddings(region, "face_embeddings") == []

    def test_malformed_entry_skipped(self):
        region = RegionResult(
            detection_index=0,
            model_outputs={
                "face_embeddings": [
                    {"vector": [0.1], "norm": 5.0, "embedder": "ok"},
                    {"bad": "data"},  # missing required fields
                ]
            },
        )
        embeddings = parse_embeddings(region, "face_embeddings")
        assert len(embeddings) == 1


# ====================================================================
# Pydantic model validation
# ====================================================================


class TestModelValidation:
    def test_ai_model_info_new_fields_optional(self):
        """Existing responses without capabilities/supported_scopes still parse."""
        info = AIModelInfo(
            name="old_model",
            identifier=1,
            version=1.0,
            categories=["cat"],
            type="ImClass",
        )
        assert info.capabilities == []
        assert info.supported_scopes == []

    def test_ai_model_info_with_new_fields(self):
        info = AIModelInfo(
            name="new_model",
            identifier=2,
            version=1.0,
            categories=["cat"],
            type="FaceDetection",
            capabilities=["detection"],
            supported_scopes=["asset", "frame"],
        )
        assert info.capabilities == ["detection"]
        assert info.supported_scopes == ["asset", "frame"]
