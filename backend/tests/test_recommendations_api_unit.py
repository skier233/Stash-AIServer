from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from stash_ai_server.api import recommendations
from stash_ai_server.recommendations.models import RecContext
from stash_ai_server.recommendations.models import RecommenderConfigField
from stash_ai_server.recommendations.models import RecommenderDefinition


def make_definition(**overrides):
    values = {
        "id": "example",
        "label": "Example",
        "contexts": [RecContext.global_feed],
        "config": [],
    }
    values.update(overrides)
    return RecommenderDefinition(**values)


def make_query(**overrides):
    values = {
        "context": RecContext.global_feed,
        "recommenderId": "example",
        "config": {},
        "seedSceneIds": None,
        "limit": None,
        "offset": 0,
    }
    values.update(overrides)
    return recommendations.RecommendationQueryBody(**values)


def test_validate_config_applies_defaults_constraints_and_warnings():
    definition = make_definition(
        config=[
            RecommenderConfigField(
                name="minimum",
                label="Minimum",
                type="number",
                default=5,
                min=1,
                max=10,
            ),
            RecommenderConfigField(
                name="slider",
                label="Slider",
                type="slider",
                default=0.5,
                min=0,
                max=1,
            ),
            RecommenderConfigField(
                name="required",
                label="Required",
                type="text",
                default=None,
                required=True,
            ),
        ]
    )

    result, warnings = recommendations._validate_config(
        definition,
        {
            "minimum": -4,
            "slider": 9,
            "required": None,
            "extra": "ignored",
        },
    )

    assert result == {"minimum": 1, "slider": 1, "required": None}
    assert warnings == [
        "config.minimum below min; clamped",
        "config.slider above max; clamped",
        "config.required required but missing",
        "config.extra ignored (undeclared)",
    ]

    result, warnings = recommendations._validate_config(
        definition, {"minimum": "invalid"}
    )
    assert result["minimum"] == 5
    assert "config.minimum invalid numeric; using default" in warnings
    assert recommendations._validate_config(make_definition(), {"free": 1}) == (
        {"free": 1},
        [],
    )


def test_filter_persistable_config_respects_field_policy():
    definition = make_definition(
        config=[
            RecommenderConfigField(name="saved", label="Saved", type="text"),
            RecommenderConfigField(
                name="temporary", label="Temporary", type="text", persist=False
            ),
        ]
    )

    assert recommendations._should_persist_field(definition.config[0]) is True
    assert recommendations._should_persist_field(definition.config[1]) is False
    assert recommendations._filter_persistable_config(
        definition, {"saved": 1, "temporary": 2, "unknown": 3}
    ) == {"saved": 1}
    assert recommendations._filter_persistable_config(make_definition(), {}) == {}


@pytest.mark.asyncio
async def test_list_recommenders_handles_empty_saved_and_stale_preferences(monkeypatch):
    monkeypatch.setattr(
        recommendations.recommender_registry, "list_for_context", lambda context: []
    )
    empty = await recommendations.list_recommenders(RecContext.global_feed, object())
    assert empty.defaultRecommenderId == ""
    assert empty.recommenders == []

    definition = make_definition()
    monkeypatch.setattr(
        recommendations.recommender_registry,
        "list_for_context",
        lambda context: [definition],
    )
    monkeypatch.setattr(
        recommendations,
        "get_preference",
        lambda db, context: SimpleNamespace(
            recommender_id="example", config={"saved": True}
        ),
    )
    saved = await recommendations.list_recommenders(
        RecContext.global_feed, object()
    )
    assert saved.defaultRecommenderId == "example"
    assert saved.savedRecommenderId == "example"
    assert saved.savedConfig == {"saved": True}

    monkeypatch.setattr(
        recommendations,
        "get_preference",
        lambda db, context: SimpleNamespace(recommender_id="removed", config={}),
    )
    stale = await recommendations.list_recommenders(
        RecContext.global_feed, object()
    )
    assert stale.savedRecommenderId is None


@pytest.mark.asyncio
async def test_query_recommendations_rejects_invalid_resolution_context_and_seed(monkeypatch):
    monkeypatch.setattr(recommendations.recommender_registry, "get", lambda rid: None)
    with pytest.raises(HTTPException) as missing:
        await recommendations.query_recommendations(make_query())
    assert missing.value.status_code == 404

    async def handler(_context, _request):
        return []

    definition = make_definition(contexts=[RecContext.similar_scene])
    monkeypatch.setattr(
        recommendations.recommender_registry,
        "get",
        lambda rid: (definition, handler),
    )
    with pytest.raises(HTTPException) as invalid_context:
        await recommendations.query_recommendations(make_query())
    assert invalid_context.value.status_code == 400

    definition = make_definition(needs_seed_scenes=True)
    monkeypatch.setattr(
        recommendations.recommender_registry,
        "get",
        lambda rid: (definition, handler),
    )
    with pytest.raises(HTTPException) as missing_seed:
        await recommendations.query_recommendations(make_query())
    assert missing_seed.value.detail == "MISSING_SEED_SCENES"


@pytest.mark.asyncio
async def test_query_recommendations_validates_scenes_limits_seeds_and_local_pagination(
    monkeypatch,
):
    captured = []

    async def handler(context, request):
        captured.append((context, request))
        return [
            {"id": 1, "paths": {}},
            {"missing": "id"},
            {"id": 2, "paths": None},
            {"id": 3, "title": "Third"},
        ]

    definition = make_definition(
        allows_multi_seed=False,
        config=[
            RecommenderConfigField(
                name="score", label="Score", type="number", default=0, min=0, max=1
            )
        ],
    )
    monkeypatch.setattr(
        recommendations.recommender_registry,
        "get",
        lambda rid: (definition, handler),
    )

    response = await recommendations.query_recommendations(
        make_query(
            config={"score": 2},
            seedSceneIds=[10, 11],
            offset=1,
            limit=1,
        )
    )

    assert captured[0][1].seedSceneIds == [10]
    assert captured[0][1].config == {"score": 1}
    assert [scene["id"] for scene in response.scenes] == [2]
    assert response.meta == {
        "total": 3,
        "offset": 1,
        "limit": 1,
        "nextOffset": 2,
        "hasMore": True,
    }
    assert response.warnings == [
        "config.score above max; clamped",
        "scene[1] validation failed",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_payload", "expected_total", "expected_more", "expected_next"),
    [
        ({"scenes": [{"id": 1}], "total": 10}, 10, True, 1),
        ({"scenes": [{"id": 1}], "has_more": True}, 2, True, 1),
        ({"scenes": [{"id": 1}], "total": 0, "has_more": False}, 1, False, None),
    ],
)
async def test_query_recommendations_trusts_handler_pagination(
    monkeypatch, handler_payload, expected_total, expected_more, expected_next
):
    async def handler(_context, _request):
        return handler_payload

    monkeypatch.setattr(
        recommendations.recommender_registry,
        "get",
        lambda rid: (make_definition(), handler),
    )

    response = await recommendations.query_recommendations(
        make_query(limit=5, offset=0)
    )

    assert response.meta["total"] == expected_total
    assert response.meta["hasMore"] is expected_more
    assert response.meta["nextOffset"] == expected_next


@pytest.mark.asyncio
async def test_query_recommendations_translates_handler_errors(monkeypatch):
    async def handler(_context, _request):
        raise RuntimeError("handler failed")

    monkeypatch.setattr(
        recommendations.recommender_registry,
        "get",
        lambda rid: (make_definition(), handler),
    )

    with pytest.raises(HTTPException) as raised:
        await recommendations.query_recommendations(make_query())

    assert raised.value.status_code == 500
    assert raised.value.detail == "recommender_execution_failed: handler failed"


@pytest.mark.asyncio
async def test_upsert_preference_validates_registry_context_and_persistence(monkeypatch):
    payload = recommendations.PreferenceUpsertBody(
        context=RecContext.global_feed,
        recommenderId="example",
        config={"saved": "yes", "temporary": "no"},
    )
    monkeypatch.setattr(recommendations.recommender_registry, "get", lambda rid: None)
    with pytest.raises(HTTPException) as missing:
        await recommendations.upsert_recommendation_preference(payload, object())
    assert missing.value.status_code == 404

    definition = make_definition(contexts=[RecContext.similar_scene])
    monkeypatch.setattr(
        recommendations.recommender_registry,
        "get",
        lambda rid: (definition, object()),
    )
    with pytest.raises(HTTPException) as invalid:
        await recommendations.upsert_recommendation_preference(payload, object())
    assert invalid.value.status_code == 400

    definition = make_definition(
        config=[
            RecommenderConfigField(name="saved", label="Saved", type="text"),
            RecommenderConfigField(
                name="temporary", label="Temporary", type="text", persist=False
            ),
        ]
    )
    captured = []
    monkeypatch.setattr(
        recommendations.recommender_registry,
        "get",
        lambda rid: (definition, object()),
    )

    def save(db, context, recommender_id, config):
        captured.append((db, context, recommender_id, config))
        return SimpleNamespace(recommender_id=recommender_id, config=config)

    monkeypatch.setattr(recommendations, "save_preference", save)
    db = object()
    response = await recommendations.upsert_recommendation_preference(payload, db)

    assert captured == [
        (db, RecContext.global_feed, "example", {"saved": "yes"})
    ]
    assert response.config == {"saved": "yes"}
