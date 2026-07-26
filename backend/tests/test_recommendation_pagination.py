"""Unit tests for recommendation pagination and cache helpers."""

from stash_ai_server.recommendations.models import RecContext, RecommendationRequest
from stash_ai_server.recommendations.utils.pagination import (
    get_cached_page,
    paginate_items,
    resolve_pagination,
    store_cache,
)


def _request(**overrides) -> RecommendationRequest:
    values = {
        "context": RecContext.global_feed,
        "recommenderId": "example",
    }
    values.update(overrides)
    return RecommendationRequest(**values)


def test_resolve_pagination_uses_defaults_and_sanitizes_values():
    assert resolve_pagination(_request()) == (0, 40)
    assert resolve_pagination(_request(offset=-5, limit=-2)) == (0, 0)
    assert resolve_pagination(_request(offset=4, limit=0)) == (4, 0)
    assert resolve_pagination(_request(offset=4, limit=12)) == (4, 12)
    assert resolve_pagination(_request(limit=None), default_limit=25) == (0, 25)


def test_resolve_pagination_handles_unvalidated_input_types():
    request = RecommendationRequest.model_construct(
        context=RecContext.global_feed,
        recommenderId="example",
        offset="bad",
        limit="bad",
    )

    assert resolve_pagination(request, default_limit=15) == (0, 15)


def test_cache_returns_none_for_missing_bucket_or_key():
    assert get_cached_page(ctx={}, cache_key="key", offset=0, limit=10) is None
    assert (
        get_cached_page(
            ctx={"_precomputed_pages": {"other": {"items": [1]}}},
            cache_key="key",
            offset=0,
            limit=10,
        )
        is None
    )


def test_store_and_get_cached_pages():
    context = {}
    store_cache(ctx=context, cache_key=("profile", 1), items=(1, 2, 3, 4))

    assert get_cached_page(
        ctx=context,
        cache_key=("profile", 1),
        offset=1,
        limit=2,
    ) == ([2, 3], 4, True)
    assert get_cached_page(
        ctx=context,
        cache_key=("profile", 1),
        offset=4,
        limit=2,
    ) == ([], 4, False)
    assert get_cached_page(
        ctx=context,
        cache_key=("profile", 1),
        offset=2,
        limit=0,
    ) == ([3, 4], 4, False)


def test_store_cache_overwrites_only_the_selected_key():
    context = {}
    store_cache(ctx=context, cache_key="first", items=[1])
    store_cache(ctx=context, cache_key="second", items=[2])
    store_cache(ctx=context, cache_key="first", items=[3, 4])

    assert get_cached_page(
        ctx=context, cache_key="first", offset=0, limit=10
    ) == ([3, 4], 2, False)
    assert get_cached_page(
        ctx=context, cache_key="second", offset=0, limit=10
    ) == ([2], 1, False)


def test_paginate_items_handles_bounds_and_unlimited_pages():
    items = ["a", "b", "c"]

    assert paginate_items(items, offset=1, limit=1) == (["b"], 3, True)
    assert paginate_items(items, offset=10, limit=2) == ([], 3, False)
    assert paginate_items(items, offset=1, limit=0) == (["b", "c"], 3, False)
