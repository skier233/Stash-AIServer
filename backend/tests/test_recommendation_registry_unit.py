"""Unit tests for recommendation registries and stored preferences."""

from enum import Enum

import pytest

from stash_ai_server.models.recommendation import RecommendationPreference
from stash_ai_server.recommendations import registry as recommendation_registry
from stash_ai_server.recommendations import storage
from stash_ai_server.recommendations.models import RecContext, RecommenderDefinition


class FakeResult:
    def __init__(self, row):
        self.row = row

    def scalar_one_or_none(self):
        return self.row


class FakeSession:
    def __init__(self, row=None):
        self.row = row
        self.added = []
        self.commits = 0
        self.refreshed = []

    def execute(self, _statement):
        return FakeResult(self.row)

    def add(self, row):
        self.added.append(row)
        self.row = row

    def commit(self):
        self.commits += 1

    def refresh(self, row):
        self.refreshed.append(row)


def _definition(identifier="example", contexts=None):
    return RecommenderDefinition(
        id=identifier,
        label=identifier,
        contexts=contexts or [RecContext.global_feed],
    )


def test_recommender_registry_registers_lists_gets_and_rejects_duplicates():
    registry = recommendation_registry._RecommenderRegistry()
    definition = _definition()

    def handler(*_args):
        return []

    registry.register(definition, handler)

    assert registry.get("example") == (definition, handler)
    assert registry.get("missing") is None
    assert registry.list_for_context(RecContext.global_feed) == [definition]
    assert registry.list_for_context(RecContext.similar_scene) == []
    with pytest.raises(ValueError, match="already registered"):
        registry.register(definition, handler)


def test_recommender_registry_unregisters_handlers_by_module_prefix():
    registry = recommendation_registry._RecommenderRegistry()

    def first(*_args):
        return []

    def second(*_args):
        return []

    first.__module__ = "plugins.first.recommender"
    second.__module__ = "plugins.second.recommender"
    registry.register(_definition("first"), first)
    registry.register(_definition("second"), second)

    registry.unregister_by_module_prefix("")
    registry.unregister_by_module_prefix("plugins.first")

    assert registry.get("first") is None
    assert registry.get("second") is not None


def test_recommender_decorator_registers_definition(monkeypatch):
    registry = recommendation_registry._RecommenderRegistry()
    monkeypatch.setattr(
        recommendation_registry,
        "recommender_registry",
        registry,
    )

    @recommendation_registry.recommender(
        id="decorated",
        label="Decorated",
        contexts=[RecContext.similar_scene],
        description="Description",
        config=[],
        needs_seed_scenes=True,
    )
    def handler(*_args):
        return []

    definition, registered_handler = registry.get("decorated")
    assert registered_handler is handler
    assert definition.description == "Description"
    assert definition.contexts == [RecContext.similar_scene]
    assert definition.needs_seed_scenes is True


def test_context_value_handles_none_enum_and_plain_values():
    class Context(Enum):
        feed = "feed"

    assert storage._context_value(None) == ""
    assert storage._context_value(Context.feed) == "feed"
    assert storage._context_value(123) == "123"


def test_get_preference_skips_empty_context_and_returns_query_result():
    session = FakeSession()
    assert storage.get_preference(session, None) is None

    row = RecommendationPreference(
        context="global_feed",
        recommender_id="example",
        config={},
    )
    session.row = row
    assert storage.get_preference(session, RecContext.global_feed) is row


def test_save_preference_creates_or_updates_rows():
    new_session = FakeSession()

    created = storage.save_preference(
        new_session,
        RecContext.global_feed,
        "first",
        None,
    )

    assert created.context == "global_feed"
    assert created.recommender_id == "first"
    assert created.config == {}
    assert new_session.added == [created]
    assert new_session.commits == 1
    assert new_session.refreshed == [created]

    existing = RecommendationPreference(
        context="global_feed",
        recommender_id="old",
        config={"old": True},
    )
    existing_session = FakeSession(existing)
    updated = storage.save_preference(
        existing_session,
        RecContext.global_feed,
        "new",
        {"limit": 10},
    )

    assert updated is existing
    assert existing.recommender_id == "new"
    assert existing.config == {"limit": 10}
    assert existing_session.added == []
    assert existing_session.commits == 1
    assert existing_session.refreshed == [existing]
