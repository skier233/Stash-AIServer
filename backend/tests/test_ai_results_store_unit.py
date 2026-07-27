from __future__ import annotations

from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from stash_ai_server.db import ai_results_store
from stash_ai_server.models.ai_results import AIModel
from stash_ai_server.models.ai_results import AIModelRun
from stash_ai_server.models.ai_results import AIModelRunModel
from stash_ai_server.models.ai_results import AIResultAggregate
from stash_ai_server.models.ai_results import AIResultTimespan


@pytest.fixture
def result_store_db(tmp_path, monkeypatch):
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'results.sqlite'}")
    for table in (
        AIModel.__table__,
        AIModelRun.__table__,
        AIModelRunModel.__table__,
        AIResultTimespan.__table__,
        AIResultAggregate.__table__,
    ):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(ai_results_store, "get_session_local", lambda: factory)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("4", 4),
        (5.8, 5),
        ("bad", None),
        (object(), None),
    ],
)
def test_ensure_int(value, expected):
    assert ai_results_store._ensure_int(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("2.5", 2.5), (3, 3.0), ("bad", None), (object(), None)],
)
def test_safe_float(value, expected):
    assert ai_results_store._safe_float(value) == expected


def test_input_and_category_normalizers():
    assert ai_results_store._prepare_input_params(None) is None
    assert ai_results_store._prepare_input_params(["not", "mapping"]) is None
    assert ai_results_store._prepare_input_params(
        {"null": "null", "nested": {"value": "null"}}
    ) == {"null": None, "nested": {"value": None}}

    assert ai_results_store._clean_category_list(None) is None
    assert ai_results_store._clean_category_list("text") is None
    assert ai_results_store._clean_category_list(
        [" Actions ", "", "null", "None", None, 4]
    ) == ["Actions", "4"]
    assert ai_results_store._clean_category_value(None) is None
    assert ai_results_store._clean_category_value(" null ") is None
    assert ai_results_store._clean_category_value(" Actions ") == "Actions"
    assert ai_results_store._clean_category_value(7) == "7"


def test_model_identifier_lookup_and_float_extraction():
    assert ai_results_store._model_identifier({"identifier": "4", "name": "One"}) == (
        4,
        "One",
    )
    assert ai_results_store._model_identifier({"model_id": 5}) == (5, None)
    assert ai_results_store._model_identifier({"external_id": "bad"}) == (None, None)
    assert ai_results_store._model_lookup_key(1, "Name") == (1, "Name")
    assert ai_results_store._extract_float(None, "value") is None
    assert ai_results_store._extract_float({}, "value") is None
    assert ai_results_store._extract_float({"value": "2.5"}, "value") == 2.5
    assert ai_results_store._extract_float({"value": "bad"}, "value") is None


def test_extract_frame_interval_prefers_metadata_then_inputs():
    assert ai_results_store._extract_frame_interval_from_run_instance(None) is None
    run = SimpleNamespace(
        result_metadata={"frame_interval": "1.5"},
        input_params={"frame_interval": 2},
    )
    assert ai_results_store._extract_frame_interval_from_run_instance(run) == 1.5
    run.result_metadata = {}
    assert ai_results_store._extract_frame_interval_from_run_instance(run) == 2


def _scene_payload(*, version=1.0, categories=None):
    return {
        "schema_version": 3,
        "duration": 20,
        "frame_interval": 2,
        "models": [
            {
                "identifier": "7",
                "name": "Detector",
                "version": version,
                "type": "tagger",
                "categories": categories or ["Actions", "null", ""],
                "input_params": {"threshold": "0.7", "null_value": "null"},
                "vendor": "local",
            },
            {
                "external_id": None,
                "name": "Nameless ID",
                "version": "invalid",
                "categories": "invalid",
            },
        ],
        "timespans": {
            " Actions ": {
                "Tag A": [
                    {"start": 0, "end": 3, "confidence": "0.9", "note": "null"},
                    {"start": 4, "confidence": 0.8},
                    "invalid",
                ],
                "Unknown": [{"start": 1, "end": 2}],
            },
            "null": {"Tag B": [{"start": 10, "end": 12, "confidence": None}]},
            "invalid": "not-a-map",
        },
    }


def test_store_and_read_scene_runs_with_real_sqlite(result_store_db):
    references = {("Tag A", "Actions"): 101, ("Tag B", None): 102}
    resolver = lambda label, category: references.get((label, category))

    run_id = ai_results_store.store_scene_run(
        service="svc",
        plugin_name="plugin",
        scene_id="9",
        input_params={"frame_interval": "3", "threshold": "0.4", "empty": "null"},
        result_payload=_scene_payload(),
        resolve_reference=resolver,
    )
    second_id = ai_results_store.store_scene_run(
        service="svc",
        plugin_name="plugin-new",
        scene_id=9,
        input_params={"frame_interval": 4},
        result_payload=_scene_payload(version=2.0, categories=["Other"]),
        resolve_reference=resolver,
    )

    assert second_id > run_id
    timespans = ai_results_store.get_scene_timespans(service="svc", scene_id=9)
    assert timespans == {
        None: {
            "102": [
                {"start": 10.0, "end": 14.0, "confidence": None},
                {"start": 10.0, "end": 14.0, "confidence": None},
            ]
        },
        "Actions": {
            "101": [
                {"start": 0.0, "end": 5.0, "confidence": 0.9},
                {"start": 0.0, "end": 5.0, "confidence": 0.9},
                {"start": 4.0, "end": 6.0, "confidence": 0.8},
                {"start": 4.0, "end": 6.0, "confidence": 0.8},
            ]
        },
    }
    assert ai_results_store.get_scene_tag_totals(service="svc", scene_id=9) == {
        101: 14.0,
        102: 8.0,
    }

    latest = ai_results_store.get_latest_scene_run(service="svc", scene_id=9)
    assert latest is not None
    assert latest.run_id == second_id
    assert latest.input_params == {"frame_interval": 4}
    assert latest.aggregates == {"Actions:101": 7.0, "102": 4.0}
    assert [model.model_name for model in latest.models] == ["Detector", "Nameless ID"]
    detector = latest.models[0]
    assert detector.version == 2.0
    assert detector.categories == ("Other",)
    assert detector.frame_interval == 2
    assert detector.threshold == 0.7

    history = ai_results_store.get_scene_model_history(service="svc", scene_id=9)
    assert [model.model_name for model in history] == ["Detector", "Nameless ID"]

    with result_store_db() as session:
        detector_row = session.execute(
            sa.select(AIModel).where(AIModel.name == "Detector")
        ).scalar_one()
        assert detector_row.plugin_name == "plugin-new"
        assert detector_row.extra == {
            "input_params": {"threshold": "0.7", "null_value": None},
            "vendor": "local",
        }


def test_scene_reads_handle_empty_results_invalid_ids_and_missing_value_ids(
    result_store_db,
):
    assert ai_results_store.get_scene_timespans(service="svc", scene_id=1) is None
    assert ai_results_store.get_scene_tag_totals(service="svc", scene_id=1) == {}
    assert ai_results_store.get_latest_scene_run(service="svc", scene_id=1) is None

    for function in (
        ai_results_store.get_scene_timespans,
        ai_results_store.get_scene_tag_totals,
        ai_results_store.get_scene_model_history,
        ai_results_store.get_latest_scene_run,
    ):
        with pytest.raises(ValueError):
            function(service="svc", scene_id="invalid")

    with result_store_db() as session:
        run = AIModelRun(
            service="svc",
            entity_type="scene",
            entity_id=2,
            status="completed",
        )
        session.add(run)
        session.flush()
        session.add_all(
            [
                AIResultTimespan(
                    run=run,
                    entity_type="scene",
                    entity_id=2,
                    payload_type="tag",
                    category="Cat",
                    value_id=None,
                    start_s=0,
                    end_s=1,
                ),
                AIResultAggregate(
                    run=run,
                    entity_type="scene",
                    entity_id=2,
                    payload_type="tag",
                    category=None,
                    str_value="fallback",
                    value_id=None,
                    metric="duration_s",
                    value_float=2,
                ),
                AIResultAggregate(
                    run=run,
                    entity_type="scene",
                    entity_id=2,
                    payload_type="tag",
                    category="ignored",
                    value_id=1,
                    metric="presence",
                    value_float=1,
                ),
            ]
        )
        session.commit()

    assert ai_results_store.get_scene_timespans(service="svc", scene_id=2) == {}
    latest = ai_results_store.get_latest_scene_run(service="svc", scene_id=2)
    assert latest is not None
    assert latest.aggregates == {"fallback": 2.0}


def test_store_image_runs_replace_categories_and_read_unique_tags(result_store_db):
    first_id = ai_results_store.store_image_run(
        service="svc",
        plugin_name="plugin",
        image_id="5",
        tag_records={"Category": [1, 2], "null": [3]},
        input_params={"threshold": "0.5"},
        requested_models=[
            {
                "model_id": 8,
                "name": "Image Model",
                "version": 1,
                "input_params": {"threshold": 0.8},
            }
        ],
    )
    second_id = ai_results_store.store_image_run(
        service="svc",
        plugin_name="plugin",
        image_id=5,
        tag_records={"Category": [2, 4], None: [3]},
    )

    assert second_id > first_id
    assert set(
        ai_results_store.get_image_tag_ids(service="svc", image_id=5)
    ) == {2, 3, 4}
    history = ai_results_store.get_image_model_history(service="svc", image_id=5)
    assert len(history) == 1
    assert history[0].model_name == "Image Model"
    assert history[0].threshold == 0.8

    with result_store_db() as session:
        old_aggregates = session.scalars(
            sa.select(AIResultAggregate).where(AIResultAggregate.run_id == first_id)
        ).all()
        assert old_aggregates == []


def test_store_image_and_read_helpers_validate_ids_and_empty_payload(result_store_db):
    with pytest.raises(ValueError):
        ai_results_store.store_image_run(
            service="svc",
            plugin_name=None,
            image_id="bad",
            tag_records={},
        )
    with pytest.raises(ValueError):
        ai_results_store.get_image_tag_ids(service="svc", image_id="bad")
    with pytest.raises(ValueError):
        ai_results_store.get_image_model_history(service="svc", image_id="bad")

    run_id = ai_results_store.store_image_run(
        service="svc",
        plugin_name=None,
        image_id=7,
        tag_records=None,
    )
    assert run_id > 0
    assert ai_results_store.get_image_tag_ids(service="svc", image_id=7) == []
    assert ai_results_store.get_image_model_history(service="svc", image_id=7) == ()


def test_purge_scene_categories_respects_empty_values_and_excluded_run(
    result_store_db,
):
    resolver = lambda label, category: 1
    first = ai_results_store.store_scene_run(
        service="svc",
        plugin_name=None,
        scene_id=3,
        input_params=None,
        result_payload={
            "timespans": {"A": {"Tag": [{"start": 0, "end": 1}]}},
        },
        resolve_reference=resolver,
    )
    second = ai_results_store.store_scene_run(
        service="svc",
        plugin_name=None,
        scene_id=3,
        input_params=None,
        result_payload={
            "timespans": {"A": {"Tag": [{"start": 2, "end": 3}]}},
        },
        resolve_reference=resolver,
    )

    ai_results_store.purge_scene_categories(
        service="svc", scene_id=3, categories=[]
    )
    ai_results_store.purge_scene_categories(
        service="svc", scene_id=3, categories=["", ""]
    )
    ai_results_store.purge_scene_categories(
        service="svc",
        scene_id=3,
        categories=["A"],
        exclude_run_id=second,
    )

    with result_store_db() as session:
        assert session.scalar(
            sa.select(sa.func.count()).select_from(AIResultTimespan).where(
                AIResultTimespan.run_id == first
            )
        ) == 0
        assert session.scalar(
            sa.select(sa.func.count()).select_from(AIResultTimespan).where(
                AIResultTimespan.run_id == second
            )
        ) == 1

    with pytest.raises(ValueError):
        ai_results_store.purge_scene_categories(
            service="svc", scene_id="bad", categories=["A"]
        )


def test_store_timespans_skips_invalid_shapes_and_unresolved_aggregates():
    added = []
    session = SimpleNamespace(add=added.append)
    run = AIModelRun(
        id=1,
        service="svc",
        entity_type="scene",
        entity_id=1,
        status="completed",
    )
    totals = ai_results_store._store_scene_timespans(
        session,
        run=run,
        scene_id=1,
        result={
            "timespans": {
                "Category": {
                    "Tag": [None, {"start": 1, "end": 0, "value": "null"}],
                    "Bad": object(),
                },
                "Bad Category": [],
            }
        },
        frame_interval=None,
        resolve_reference=lambda label, category: None,
    )
    assert totals == {("Category", "Tag"): 1.0}
    assert len(added) == 1
    assert added[0].value_json == {"value": None}

    ai_results_store._store_aggregates(
        session,
        run=run,
        scene_id=1,
        totals={("Category", "Unresolved"): 1, ("Category", "Resolved"): 2},
        resolve_reference=lambda label, category: 5 if label == "Resolved" else None,
    )
    assert len(added) == 2
    assert added[-1].value_id == 5


@pytest.mark.asyncio
async def test_async_wrappers_delegate_to_synchronous_operations(
    monkeypatch,
):
    calls = []

    monkeypatch.setattr(
        ai_results_store,
        "get_scene_timespans",
        lambda **kwargs: calls.append(("timespans", kwargs)) or {"category": {}},
    )
    monkeypatch.setattr(
        ai_results_store,
        "get_scene_tag_totals",
        lambda **kwargs: calls.append(("totals", kwargs)) or {1: 2.0},
    )
    monkeypatch.setattr(
        ai_results_store,
        "store_scene_run",
        lambda **kwargs: calls.append(("store_scene", kwargs)) or 10,
    )
    monkeypatch.setattr(
        ai_results_store,
        "store_image_run",
        lambda **kwargs: calls.append(("store_image", kwargs)) or 11,
    )
    monkeypatch.setattr(
        ai_results_store,
        "get_image_tag_ids",
        lambda **kwargs: calls.append(("image_tags", kwargs)) or [1],
    )
    monkeypatch.setattr(
        ai_results_store,
        "get_latest_scene_run",
        lambda **kwargs: calls.append(("latest", kwargs)) or None,
    )
    monkeypatch.setattr(
        ai_results_store,
        "get_scene_model_history",
        lambda **kwargs: calls.append(("scene_history", kwargs)) or (),
    )
    monkeypatch.setattr(
        ai_results_store,
        "get_image_model_history",
        lambda **kwargs: calls.append(("image_history", kwargs)) or (),
    )

    assert await ai_results_store.get_scene_timespans_async(
        service="svc", scene_id=1
    ) == {"category": {}}
    assert await ai_results_store.get_scene_tag_totals_async(
        service="svc", scene_id=1
    ) == {1: 2.0}
    assert await ai_results_store.store_scene_run_async(
        service="svc",
        plugin_name=None,
        scene_id=1,
        input_params=None,
        result_payload={},
    ) == 10
    assert await ai_results_store.store_image_run_async(
        service="svc",
        plugin_name=None,
        image_id=1,
        tag_records={},
    ) == 11
    assert await ai_results_store.get_image_tag_ids_async(
        service="svc", image_id=1
    ) == [1]
    assert await ai_results_store.get_latest_scene_run_async(
        service="svc", scene_id=1
    ) is None
    assert await ai_results_store.get_scene_model_history_async(
        service="svc", scene_id=1
    ) == ()
    assert await ai_results_store.get_image_model_history_async(
        service="svc", image_id=1
    ) == ()
    assert len(calls) == 8
