from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from stash_ai_server.recommendations.utils import scene_fetch
from stash_ai_server.recommendations.utils import tag_profiles
from stash_ai_server.recommendations.utils import timespan_metrics
from stash_ai_server.recommendations.utils import watch_history


class FakeSession:
    def __init__(self, rows=(), scalar=None, error: Exception | None = None):
        self.rows = rows
        self.scalar = scalar
        self.error = error
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, statement):
        self.statements.append(statement)
        if self.error is not None:
            raise self.error
        return FakeResult(self.rows, self.scalar)


class FakeResult(list):
    def __init__(self, rows=(), scalar=None):
        super().__init__(rows)
        self._scalar = scalar

    def scalar_one_or_none(self):
        return self._scalar


def install_session(monkeypatch, module, *, rows=(), scalar=None, error=None):
    sessions = []

    def factory():
        session = FakeSession(rows=rows, scalar=scalar, error=error)
        sessions.append(session)
        return session

    monkeypatch.setattr(module, "get_session_local", lambda: factory)
    return sessions


def test_scene_payload_helpers_fill_defaults_without_overwriting_values():
    stub = scene_fetch._stub_scene("7")
    assert stub["id"] == 7
    assert stub["title"] == "Scene 7"

    payload = {"paths": {"preview": "preview"}, "performers": [{"id": 1}]}
    normalized = scene_fetch._normalize_scene_payload(payload)
    assert normalized["paths"] == {
        "preview": "preview",
        "screenshot": None,
        "stream": None,
        "webp": None,
    }
    assert normalized["performers"] == [{"id": 1}]
    assert normalized["tags"] == []
    assert normalized["files"] == []
    assert normalized["series"] == []


def test_scene_column_helpers_support_missing_and_alternative_columns():
    metadata = sa.MetaData()
    table = sa.Table(
        "items",
        metadata,
        sa.Column("identifier", sa.Integer),
        sa.Column("title", sa.String),
    )

    assert scene_fetch._pick_column(table, "missing", "identifier") is table.c.identifier
    assert scene_fetch._pick_column(None, "identifier") is None
    assert scene_fetch._pick_column(table, "missing") is None
    assert scene_fetch._column_with_default(table, "title").name == "title"
    assert scene_fetch._column_with_default(table, "missing", alias="fallback").name == "fallback"
    assert scene_fetch._label_or_literal(table.c.identifier, "id").name == "id"
    assert scene_fetch._label_or_literal(None, "empty").name == "empty"

    class BrokenColumn:
        def label(self, _alias):
            raise RuntimeError("cannot label")

    assert scene_fetch._label_or_literal(BrokenColumn(), "safe").name == "safe"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("  ", None),
        (12, 12),
        (12.9, 12),
        ("123", 123),
        ("2024-01-02T03:04:05Z", 1704164645),
        ("2024-01-02T03:04:05", 1704164645),
        ("invalid", None),
        (object(), None),
    ],
)
def test_coerce_unix_timestamp(raw, expected):
    assert scene_fetch._coerce_unix_timestamp(raw) == expected


def test_scene_urls_include_filtered_params_and_api_key(monkeypatch):
    monkeypatch.setattr(
        scene_fetch,
        "stash_api",
        SimpleNamespace(api_key="shared key"),
    )

    assert scene_fetch._build_scene_url(4, "preview") == "/scene/4/preview"
    assert scene_fetch._build_scene_url(
        4,
        "stream",
        include_api_key=True,
        params={"empty": "", "quality": "720p"},
    ) == "/scene/4/stream?quality=720p&apikey=shared+key"
    assert scene_fetch._build_scene_paths(4) == {
        "screenshot": "/scene/4/screenshot",
        "preview": "/scene/4/preview",
        "stream": "/scene/4/stream?apikey=shared+key",
        "webp": "/scene/4/webp",
    }
    assert scene_fetch._build_performer_image_url(
        9, updated_at="2024-01-02T03:04:05Z"
    ) == "/performer/9/image?default=true&t=1704164645"
    assert scene_fetch._build_performer_image_url(
        9, updated_at="invalid"
    ) == "/performer/9/image?default=true"


def test_fetch_scene_candidates_handles_empty_and_unavailable_dependencies(monkeypatch):
    assert scene_fetch.fetch_scene_candidates_by_performers(performer_ids=[]) == []

    monkeypatch.setattr(scene_fetch.stash_db, "get_stash_sessionmaker", lambda: None)
    assert scene_fetch.fetch_scene_candidates_by_performers(performer_ids=[1]) == []

    monkeypatch.setattr(scene_fetch.stash_db, "get_stash_sessionmaker", lambda: object())
    monkeypatch.setattr(
        scene_fetch.stash_db, "get_first_available_table", lambda *args, **kwargs: None
    )
    assert scene_fetch.fetch_scene_candidates_by_performers(performer_ids=[1]) == []

    metadata = sa.MetaData()
    incomplete = sa.Table("links", metadata, sa.Column("scene_id", sa.Integer))
    monkeypatch.setattr(
        scene_fetch.stash_db,
        "get_first_available_table",
        lambda *args, **kwargs: incomplete,
    )
    assert scene_fetch.fetch_scene_candidates_by_performers(performer_ids=[1]) == []


def test_fetch_scene_candidates_filters_sorts_and_limits(monkeypatch):
    metadata = sa.MetaData()
    links = sa.Table(
        "performers_scenes",
        metadata,
        sa.Column("scene_id", sa.Integer),
        sa.Column("performer_id", sa.Integer),
    )
    rows = [
        SimpleNamespace(scene_id="10", performer_id="1"),
        SimpleNamespace(scene_id=10, performer_id=2),
        SimpleNamespace(scene_id=20, performer_id=1),
        SimpleNamespace(scene_id=30, performer_id=1),
        SimpleNamespace(scene_id="bad", performer_id=1),
    ]
    session = FakeSession(rows)
    monkeypatch.setattr(
        scene_fetch.stash_db, "get_stash_sessionmaker", lambda: lambda: session
    )
    monkeypatch.setattr(
        scene_fetch.stash_db,
        "get_first_available_table",
        lambda *args, **kwargs: links,
    )

    result = scene_fetch.fetch_scene_candidates_by_performers(
        performer_ids=[1, 2],
        exclude_scene_ids=[30],
        limit=2,
    )

    assert result == [(10, {1, 2}), (20, {1})]
    assert "LIMIT" in str(session.statements[0])


def _install_stash_schema(monkeypatch):
    metadata = sa.MetaData()
    tables = {}

    tables["scenes"] = sa.Table(
        "scenes",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("title", sa.String),
        sa.Column("rating", sa.Float),
        sa.Column("studio_id", sa.Integer),
        sa.Column("screenshot_path", sa.String),
        sa.Column("preview_path", sa.String),
        sa.Column("duration_ms", sa.Float),
        sa.Column("play_duration", sa.Float),
    )
    tables["studios"] = sa.Table(
        "studios",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String),
    )
    tables["performers"] = sa.Table(
        "performers",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String),
        sa.Column("image_path", sa.String),
        sa.Column("updated_at", sa.String),
    )
    tables["performers_scenes"] = sa.Table(
        "performers_scenes",
        metadata,
        sa.Column("scene_id", sa.Integer),
        sa.Column("performer_id", sa.Integer),
    )
    tables["tags"] = sa.Table(
        "tags",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String),
    )
    tables["scene_tags"] = sa.Table(
        "scene_tags",
        metadata,
        sa.Column("scene_id", sa.Integer),
        sa.Column("tag_id", sa.Integer),
    )
    tables["scene_groups"] = sa.Table(
        "scene_groups",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String),
    )
    tables["scene_groups_scenes"] = sa.Table(
        "scene_groups_scenes",
        metadata,
        sa.Column("scene_id", sa.Integer),
        sa.Column("scene_group_id", sa.Integer),
    )
    tables["folders"] = sa.Table(
        "folders",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("path", sa.String),
    )
    tables["files"] = sa.Table(
        "files",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("basename", sa.String),
        sa.Column("parent_folder_id", sa.Integer),
        sa.Column("size", sa.Integer),
    )
    tables["video_files"] = sa.Table(
        "video_files",
        metadata,
        sa.Column("file_id", sa.Integer, primary_key=True),
        sa.Column("duration", sa.Float),
        sa.Column("width", sa.Integer),
        sa.Column("height", sa.Integer),
    )
    tables["scenes_files"] = sa.Table(
        "scenes_files",
        metadata,
        sa.Column("scene_id", sa.Integer),
        sa.Column("file_id", sa.Integer),
        sa.Column("primary", sa.Boolean),
    )
    tables["files_fingerprints"] = sa.Table(
        "files_fingerprints",
        metadata,
        sa.Column("file_id", sa.Integer),
        sa.Column("type", sa.String),
        sa.Column("value", sa.LargeBinary),
    )

    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            tables["scenes"].insert(),
            [
                {
                    "id": 1,
                    "title": "First",
                    "rating": 88.6,
                    "studio_id": 5,
                    "screenshot_path": "ignored",
                    "preview_path": "ignored",
                    "duration_ms": 123000,
                    "play_duration": 0,
                },
                {
                    "id": 2,
                    "title": "Second",
                    "rating": None,
                    "studio_id": None,
                    "screenshot_path": None,
                    "preview_path": None,
                    "duration_ms": 0,
                    "play_duration": 30,
                },
            ],
        )
        connection.execute(tables["studios"].insert(), [{"id": 5, "name": "Studio"}])
        connection.execute(
            tables["performers"].insert(),
            [
                {
                    "id": 11,
                    "name": "Alice",
                    "image_path": None,
                    "updated_at": "2024-01-02T03:04:05Z",
                }
            ],
        )
        connection.execute(
            tables["performers_scenes"].insert(),
            [{"scene_id": 1, "performer_id": 11}],
        )
        connection.execute(tables["tags"].insert(), [{"id": 21, "name": "Tag"}])
        connection.execute(
            tables["scene_tags"].insert(), [{"scene_id": 1, "tag_id": 21}]
        )
        connection.execute(
            tables["scene_groups"].insert(), [{"id": 31, "name": "Series"}]
        )
        connection.execute(
            tables["scene_groups_scenes"].insert(),
            [{"scene_id": 1, "scene_group_id": 31}],
        )
        connection.execute(
            tables["folders"].insert(), [{"id": 41, "path": "/media"}]
        )
        connection.execute(
            tables["files"].insert(),
            [{"id": 51, "basename": "video.mp4", "parent_folder_id": 41, "size": 99}],
        )
        connection.execute(
            tables["video_files"].insert(),
            [{"file_id": 51, "duration": 125.5, "width": 1920, "height": 1080}],
        )
        connection.execute(
            tables["scenes_files"].insert(),
            [{"scene_id": 1, "file_id": 51, "primary": True}],
        )
        connection.execute(
            tables["files_fingerprints"].insert(),
            [{"file_id": 51, "type": "md5", "value": b"\x01\x02"}],
        )

    session_factory = sessionmaker(bind=engine)

    def get_table(name, required=False):
        return tables.get(name)

    def get_first(*names, required_columns=None):
        for name in names:
            table = tables.get(name)
            if table is None:
                continue
            if required_columns and not all(table.c.get(column) is not None for column in required_columns):
                continue
            return table
        return None

    monkeypatch.setattr(scene_fetch.stash_db, "get_stash_sessionmaker", lambda: session_factory)
    monkeypatch.setattr(scene_fetch.stash_db, "get_stash_table", get_table)
    monkeypatch.setattr(scene_fetch.stash_db, "get_first_available_table", get_first)
    monkeypatch.setattr(
        scene_fetch,
        "stash_api",
        SimpleNamespace(api_key="key"),
    )
    return tables, engine


def test_fetch_scenes_via_real_in_memory_schema(monkeypatch):
    _, engine = _install_stash_schema(monkeypatch)

    try:
        scenes = scene_fetch._fetch_scenes_via_db([1, 2, 999])

        assert set(scenes) == {1, 2}
        assert scenes[1]["title"] == "First"
        assert scenes[1]["rating100"] == 89
        assert scenes[1]["studio"] == {"id": 5, "name": "Studio"}
        assert scenes[1]["duration"] == 125.5
        assert scenes[1]["paths"]["stream"] == "/scene/1/stream?apikey=key"
        assert scenes[1]["performers"] == [
            {
                "id": 11,
                "name": "Alice",
                "image_path": "/performer/11/image?default=true&t=1704164645",
            }
        ]
        assert scenes[1]["tags"] == [{"id": 21, "name": "Tag"}]
        assert scenes[1]["series"] == [{"id": 31, "name": "Series"}]
        assert scenes[1]["files"] == [
            {
                "id": 51,
                "path": "/media/video.mp4",
                "width": 1920,
                "height": 1080,
                "duration": 125.5,
                "size": 99,
                "primary": True,
                "fingerprints": [{"type": "md5", "value": "0102"}],
            }
        ]
        assert scenes[2]["duration"] == 30.0
        assert scenes[2]["studio"] is None
    finally:
        engine.dispose()


def test_fetch_scenes_via_db_handles_early_returns_and_errors(monkeypatch):
    assert scene_fetch._fetch_scenes_via_db([]) == {}

    monkeypatch.setattr(scene_fetch.stash_db, "get_stash_sessionmaker", lambda: None)
    assert scene_fetch._fetch_scenes_via_db([1]) == {}

    monkeypatch.setattr(
        scene_fetch.stash_db, "get_stash_sessionmaker", lambda: lambda: FakeSession()
    )
    monkeypatch.setattr(scene_fetch.stash_db, "get_stash_table", lambda *args, **kwargs: None)
    assert scene_fetch._fetch_scenes_via_db([1]) == {}

    metadata = sa.MetaData()
    table_without_id = sa.Table("scenes", metadata, sa.Column("title", sa.String))
    monkeypatch.setattr(
        scene_fetch.stash_db,
        "get_stash_table",
        lambda *args, **kwargs: table_without_id,
    )
    assert scene_fetch._fetch_scenes_via_db([1]) == {}

    metadata = sa.MetaData()
    scenes = sa.Table("scenes", metadata, sa.Column("id", sa.Integer))
    monkeypatch.setattr(scene_fetch.stash_db, "get_stash_table", lambda *args, **kwargs: scenes)
    monkeypatch.setattr(
        scene_fetch.stash_db,
        "get_stash_sessionmaker",
        lambda: lambda: FakeSession(error=RuntimeError("database failed")),
    )
    assert scene_fetch._fetch_scenes_via_db([1]) == {}


def test_fetch_scenes_by_ids_combines_database_results_and_stubs(monkeypatch):
    monkeypatch.setattr(
        scene_fetch,
        "_fetch_scenes_via_db",
        lambda ids: {1: {"id": 1, "title": "Found", "paths": {"preview": "p"}}},
    )

    result = scene_fetch.fetch_scenes_by_ids([1, 2, 1])

    assert list(result) == [1, 2]
    assert result[1]["title"] == "Found"
    assert result[1]["paths"]["preview"] == "p"
    assert result[2]["title"] == "Scene 2"
    assert scene_fetch.fetch_scenes_by_ids([]) == {}


def test_collect_tag_durations_handles_rows_and_invalid_values(monkeypatch):
    assert tag_profiles.fetch_tag_durations_for_scenes(service="svc", scene_ids=[]) == (
        {},
        set(),
    )

    rows = [
        SimpleNamespace(scene_id="1", tag_id="10", duration_s="2.5"),
        SimpleNamespace(scene_id=1, tag_id=11, duration_s=0),
        SimpleNamespace(scene_id=2, tag_id=None, duration_s=3),
        SimpleNamespace(scene_id="bad", tag_id=12, duration_s=3),
        SimpleNamespace(scene_id=3, tag_id=13, duration_s=-1),
    ]
    install_session(monkeypatch, tag_profiles, rows=rows)

    result = tag_profiles.fetch_tag_durations_for_scenes(
        service="svc", scene_ids=[1, 2, None]
    )

    assert result == ({1: {10: 2.5}}, {10})


def test_accumulate_and_build_watched_tag_profiles(monkeypatch):
    target = {1: 2.0}
    tag_profiles._accumulate_tag_durations(
        target, {1: "3", 2: 0, 3: "bad", 4: 4.5}
    )
    assert target == {1: 5.0, 4: 4.5}
    tag_profiles._accumulate_tag_durations(target, {})

    watch_results = {
        1: ({"10": "2.5", "bad": 4, 11: -1}, 5),
        2: ({12: 3}, 4),
    }
    monkeypatch.setattr(
        tag_profiles,
        "collect_watched_segment_tag_durations",
        lambda *, scene_id, **kwargs: watch_results[scene_id],
    )
    monkeypatch.setattr(
        tag_profiles,
        "get_scene_tag_totals",
        lambda *, scene_id, **kwargs: {20: 6} if scene_id == 1 else {},
    )

    watched, total, breakdown = tag_profiles.build_watched_tag_profile(
        service="svc", scene_ids=[1, "invalid", 2]
    )
    assert watched == {10: 2.5, 12: 3.0}
    assert total == 9
    assert breakdown[1]["watch_tags"] == {10: 2.5}

    full, total, breakdown = tag_profiles.build_watched_tag_profile(
        service="svc", scene_ids=[1, 2], prefer_full_scene=True, min_confidence=0.5
    )
    assert full == {20: 6.0, 12: 3.0}
    assert total == 9
    assert breakdown[1]["fallback_tags"] == {20: 6.0}


def test_tag_frequency_and_total_queries(monkeypatch):
    assert tag_profiles.fetch_tag_document_frequencies(service="svc", tag_ids=[]) == {}

    rows = [
        SimpleNamespace(tag_id="1", scene_count="4"),
        SimpleNamespace(tag_id="bad", scene_count=2),
    ]
    install_session(monkeypatch, tag_profiles, rows=rows)
    assert tag_profiles.fetch_tag_document_frequencies(
        service="svc", tag_ids=[1, None]
    ) == {1: 4}

    install_session(monkeypatch, tag_profiles, scalar="12")
    assert tag_profiles.fetch_total_tagged_scene_count(service="svc") == 12
    install_session(monkeypatch, tag_profiles, scalar="bad")
    assert tag_profiles.fetch_total_tagged_scene_count(service="svc") == 0


def test_collect_tag_durations_builds_aggregated_map(monkeypatch):
    assert timespan_metrics.collect_tag_durations(service="svc", tag_ids=[]) == {}

    rows = [(1, 10, 2.5), ("1", "10", 1.5), (2, 11, None)]
    sessions = install_session(monkeypatch, timespan_metrics, rows=rows)

    result = timespan_metrics.collect_tag_durations(
        service="svc", tag_ids=[10, 11], scene_ids=[1, None]
    )

    assert result == {1: {10: 4.0}, 2: {11: 0.0}}
    assert sessions[0].statements


def test_interval_operations_cover_overlap_adjacency_and_empty_inputs():
    assert timespan_metrics.merge_intervals([]) == []
    assert timespan_metrics.merge_intervals(
        [(5, 8), (1, 3), (3, 4), (7, 10)]
    ) == [(1, 4), (5, 10)]
    assert timespan_metrics.intersect_two(
        [(0, 5), (8, 12)], [(3, 9), (10, 15)]
    ) == [(3, 5), (8, 9), (10, 12)]
    assert timespan_metrics.intersect_all([]) == []
    assert timespan_metrics.intersect_all(
        [[(0, 10)], [(2, 8)], [(4, 6)]]
    ) == [(4, 6)]
    assert timespan_metrics.intersect_all([[(0, 1)], [(2, 3)]]) == []


def test_compute_cooccurrence_duration(monkeypatch):
    assert timespan_metrics.compute_cooccurrence_duration(
        service="svc", scene_id=1, tag_ids=[]
    ) == 0

    monkeypatch.setattr(timespan_metrics, "get_scene_timespans", lambda **kwargs: {})
    assert timespan_metrics.compute_cooccurrence_duration(
        service="svc", scene_id=1, tag_ids=[1]
    ) == 0

    buckets = {
        "category": {
            "1": [{"start": 0, "end": 5}, {"start": 8, "end": 10}],
            "2": [{"start": 3, "end": 9}, {"start": 12, "end": 12}],
        }
    }
    monkeypatch.setattr(
        timespan_metrics, "get_scene_timespans", lambda **kwargs: buckets
    )
    assert timespan_metrics.compute_cooccurrence_duration(
        service="svc", scene_id=1, tag_ids=[1, 2]
    ) == 3
    assert timespan_metrics.compute_cooccurrence_duration(
        service="svc", scene_id=1, tag_ids=[1, 99]
    ) == 0


def test_fetch_scene_watch_intervals_repairs_and_filters_rows(monkeypatch):
    rows = [
        (0, 5, 5),
        (4, 8, 4),
        (10, 10, 3),
        ("bad", 20, 1),
        (30, "bad", 2),
        (40, 40, 0),
    ]
    install_session(monkeypatch, timespan_metrics, rows=rows)

    assert timespan_metrics._fetch_scene_watch_intervals(1) == [
        (0.0, 8.0),
        (10.0, 13.0),
        (30.0, 32.0),
    ]


def test_collect_watched_segment_tag_durations(monkeypatch):
    monkeypatch.setattr(timespan_metrics, "_fetch_scene_watch_intervals", lambda scene_id: [])
    assert timespan_metrics.collect_watched_segment_tag_durations(
        service="svc", scene_id=1
    ) == ({}, 0.0)

    monkeypatch.setattr(
        timespan_metrics, "_fetch_scene_watch_intervals", lambda scene_id: [(0, 10)]
    )
    monkeypatch.setattr(timespan_metrics, "get_scene_timespans", lambda **kwargs: {})
    assert timespan_metrics.collect_watched_segment_tag_durations(
        service="svc", scene_id=1
    ) == ({}, 10)

    buckets = {
        "one": {
            "10": [
                {"start": 1, "end": 4, "confidence": 0.9},
                {"start": 3, "end": 8, "confidence": 0.8},
                {"start": 9, "end": 11, "confidence": 0.1},
                {"start": "bad", "end": 4, "confidence": 1},
                "not-a-dict",
            ],
            "bad-tag": [{"start": 1, "end": 2}],
            "11": [{"start": 2, "end": "bad", "confidence": 1}],
            "12": [],
        },
        "two": {"10": [{"start": 20, "end": 25, "confidence": 1}]},
    }
    monkeypatch.setattr(
        timespan_metrics, "get_scene_timespans", lambda **kwargs: buckets
    )

    assert timespan_metrics.collect_watched_segment_tag_durations(
        service="svc", scene_id=1, min_confidence="0.5"
    ) == ({10: 7.0}, 10)
    assert timespan_metrics.collect_watched_segment_tag_durations(
        service="svc", scene_id=1, min_confidence="invalid"
    )[0][10] == 8.0


def test_ensure_utc_handles_none_naive_and_aware_datetimes():
    naive = datetime(2024, 1, 1, 12)
    eastern = timezone(timedelta(hours=-5))
    aware = datetime(2024, 1, 1, 7, tzinfo=eastern)

    assert watch_history._ensure_utc(None) is None
    assert watch_history._ensure_utc(naive) == naive.replace(tzinfo=timezone.utc)
    assert watch_history._ensure_utc(aware) == datetime(
        2024, 1, 1, 12, tzinfo=timezone.utc
    )


def test_load_watch_history_summary_filters_rows_and_normalizes_dates(monkeypatch):
    entered = datetime(2024, 1, 1, 12)
    rows = [
        SimpleNamespace(
            scene_id="1",
            watched_s="12.5",
            last_left=None,
            last_entered=entered,
            last_segment=None,
        ),
        SimpleNamespace(
            scene_id=2,
            watched_s=0,
            last_left=entered,
            last_entered=None,
            last_segment=None,
        ),
        SimpleNamespace(
            scene_id=3,
            watched_s=3,
            last_left=None,
            last_entered=None,
            last_segment=entered,
        ),
    ]
    sessions = install_session(monkeypatch, watch_history, rows=rows)

    result = watch_history.load_watch_history_summary(
        recent_cutoff=entered,
        min_watch_seconds=2,
        limit=5,
        order_desc=False,
    )

    assert result == [
        {
            "scene_id": 1,
            "watched_s": 12.5,
            "last_seen": entered.replace(tzinfo=timezone.utc),
        },
        {
            "scene_id": 3,
            "watched_s": 3.0,
            "last_seen": entered.replace(tzinfo=timezone.utc),
        },
    ]
    assert "LIMIT" in str(sessions[0].statements[0])


def test_load_recent_watch_scene_ids_delegates_to_summary(monkeypatch):
    monkeypatch.setattr(
        watch_history,
        "load_watch_history_summary",
        lambda **kwargs: [{"scene_id": 1}, {"scene_id": 2}, {"scene_id": 1}],
    )
    assert watch_history.load_recent_watch_scene_ids(limit=2) == {1, 2}
