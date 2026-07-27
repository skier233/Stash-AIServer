from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from stash_ai_server.models.interaction import ImageDerived
from stash_ai_server.models.interaction import InteractionEvent
from stash_ai_server.models.interaction import InteractionLibrarySearch
from stash_ai_server.models.interaction import InteractionSession
from stash_ai_server.models.interaction import InteractionSessionAlias
from stash_ai_server.models.interaction import SceneDerived
from stash_ai_server.models.interaction import SceneWatch
from stash_ai_server.models.interaction import SceneWatchSegment
from stash_ai_server.schemas.interaction import InteractionEventIn
from stash_ai_server.services import interactions


@pytest.fixture
def interaction_db(tmp_path, monkeypatch):
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'interactions.sqlite'}")
    for table in (
        InteractionSession.__table__,
        InteractionSessionAlias.__table__,
        InteractionEvent.__table__,
        SceneWatch.__table__,
        SceneWatchSegment.__table__,
        SceneDerived.__table__,
        ImageDerived.__table__,
        InteractionLibrarySearch.__table__,
    ):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    values = {
        "INTERACTION_MERGE_TTL_SECONDS": 120,
        "INTERACTION_MIN_SESSION_MINUTES": 0,
        "INTERACTION_SEGMENT_TIME_MARGIN_SECONDS": 0.1,
        "INTERACTION_SEGMENT_POS_MARGIN_SECONDS": 0.5,
        "SEGMENT_MERGE_GAP_SECONDS": 1,
        "SEGMENT_MIN_DURATION_SECONDS": 0.1,
    }
    monkeypatch.setattr(
        interactions, "sys_get", lambda key, default=None: values.get(key, default)
    )
    try:
        yield factory
    finally:
        engine.dispose()


def event(
    event_id,
    *,
    session_id="session",
    seconds=0,
    event_type="scene_view",
    entity_type="scene",
    entity_id=1,
    metadata=None,
):
    return InteractionEventIn(
        id=event_id,
        session_id=session_id,
        ts=datetime(2024, 1, 1, 12, tzinfo=timezone.utc)
        + timedelta(seconds=seconds),
        type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        metadata=metadata,
    )


def test_datetime_entity_and_synthetic_event_helpers():
    naive = datetime(2024, 1, 1, 12)
    aware = naive.replace(tzinfo=timezone(timedelta(hours=-5)))
    assert interactions._to_naive(None) is None
    assert interactions._to_naive(naive) is naive
    assert interactions._to_naive(aware) == datetime(2024, 1, 1, 17)

    assert interactions._sanitize_entity_id("4") == 4
    assert interactions._sanitize_entity_id("bad") == 0
    assert interactions._sanitize_entity_id(interactions.PG_INT_MAX + 1) == 0
    assert interactions._sanitize_entity_id(-interactions.PG_INT_MAX - 1) == 0

    synthetic = interactions._SyntheticInteractionEvent(
        client_event_id="event",
        session_id="session",
        event_type="scene_watch_progress",
        entity_type="scene",
        entity_id=1,
        client_ts=naive,
        event_metadata={"position": 2},
    )
    assert synthetic.id is None
    assert synthetic.event_metadata == {"position": 2}


def test_recompute_segments_from_rows_covers_controls_progress_seek_and_filtering():
    def row(kind, metadata):
        return SimpleNamespace(event_type=kind, event_metadata=metadata)

    rows = [
        row("scene_watch_start", {"position": 0}),
        row("scene_watch_progress", {"position": 4}),
        row("scene_watch_pause", {"position": 5}),
        row("scene_seek", {"from": 5, "to": 10}),
        row("scene_watch_start", {}),
        row("scene_seek", {"from": 14, "to": 20}),
        row("scene_watch_progress", {"position": 24}),
        row("scene_watch_complete", {"position": 25}),
        row("scene_watch_start", {"position": 30}),
        row("scene_seek", {"from": "bad", "to": "bad"}),
        row("scene_watch_start", {"position": 40}),
        row("scene_watch_progress", {"position": 42}),
    ]

    segments = interactions.recompute_segments_from_rows(
        rows,
        "session",
        1,
        7,
        merge_gap=1,
        min_duration=3,
    )

    assert [(segment.start_s, segment.end_s, segment.watched_s) for segment in segments] == [
        (0.0, 5.0, 5.0),
        (10.0, 14.0, 4.0),
        (20.0, 25.0, 5.0),
    ]


def test_recompute_segments_queries_rows_and_uses_config(interaction_db, monkeypatch):
    with interaction_db() as db:
        db.add(
            InteractionSession(
                session_id="session",
                last_event_ts=datetime(2024, 1, 1),
                session_start_ts=datetime(2024, 1, 1),
            )
        )
        db.add_all(
            [
                InteractionEvent(
                    client_event_id="a",
                    session_id="session",
                    event_type="scene_watch_start",
                    entity_type="scene",
                    entity_id=1,
                    client_ts=datetime(2024, 1, 1),
                    event_metadata={"position": 0},
                ),
                InteractionEvent(
                    client_event_id="b",
                    session_id="session",
                    event_type="scene_watch_pause",
                    entity_type="scene",
                    entity_id=1,
                    client_ts=datetime(2024, 1, 1, 0, 0, 1),
                    event_metadata={"position": 4},
                ),
            ]
        )
        db.commit()
        assert interactions.recompute_segments(db, "session", 1, 9)[0].watched_s == 4

    monkeypatch.setattr(
        interactions,
        "sys_get",
        lambda key, default=None: "invalid"
        if key == "SEGMENT_MIN_DURATION_SECONDS"
        else default,
    )
    with interaction_db() as db:
        assert interactions.recompute_segments(db, "missing", 1, 9) == []


def test_update_session_tracks_entities_and_session_metadata(interaction_db):
    with interaction_db() as db:
        session = InteractionSession(
            session_id="session",
            last_event_ts=datetime(2024, 1, 1),
            session_start_ts=datetime(2024, 1, 1),
        )
        db.add(session)
        db.commit()

        scene_event = SimpleNamespace(
            id=1,
            session_id="session",
            client_ts=datetime(2024, 1, 2, tzinfo=timezone.utc),
            entity_type="scene",
            entity_id=4,
            event_metadata={},
        )
        interactions._update_session(db, scene_event)
        assert session.last_entity_type == "scene"
        assert session.last_entity_id == 4

        session_event = SimpleNamespace(
            id=2,
            session_id="session",
            client_ts=datetime(2024, 1, 3),
            entity_type="session",
            entity_id=0,
            event_metadata={
                "last_entity": {
                    "type": "image",
                    "id": 8,
                    "ts": "1704240000000",
                }
            },
        )
        interactions._update_session(db, session_event, session)
        assert session.last_entity_type == "image"
        assert session.last_entity_id == 8
        assert session.last_entity_event_ts == datetime.utcfromtimestamp(1704240000)

        missing = SimpleNamespace(
            id=3,
            session_id="missing",
            client_ts=datetime(2024, 1, 3),
            entity_type="scene",
            entity_id=1,
            event_metadata={},
        )
        with pytest.raises(ValueError):
            interactions._update_session(db, missing)


def test_find_or_create_session_supports_direct_alias_fingerprint_and_new_paths(
    interaction_db,
):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with interaction_db() as db:
        db.add_all(
            [
                InteractionSession(
                    session_id="direct",
                    last_event_ts=now,
                    session_start_ts=now,
                ),
                InteractionSession(
                    session_id="canonical",
                    last_event_ts=now,
                    session_start_ts=now,
                    client_fingerprint="fingerprint",
                ),
                InteractionSessionAlias(
                    alias_session_id="alias", canonical_session_id="canonical"
                ),
            ]
        )
        db.commit()

        assert interactions._find_or_create_session_id(db, "direct", None) == "direct"
        assert interactions._find_or_create_session_id(db, "alias", None) == "canonical"
        assert (
            interactions._find_or_create_session_id(
                db, "fingerprint-alias", "fingerprint"
            )
            == "canonical"
        )
        assert (
            db.get(InteractionSessionAlias, "fingerprint-alias").canonical_session_id
            == "canonical"
        )
        assert interactions._find_or_create_session_id(db, "new", None) == "new"
        assert db.scalar(
            sa.select(sa.func.count()).select_from(InteractionSession).where(
                InteractionSession.session_id == "new"
            )
        ) == 1


def test_finalize_stale_sessions_updates_existing_and_new_derived_rows(interaction_db):
    old = datetime(2024, 1, 1)
    with interaction_db() as db:
        db.add_all(
            [
                InteractionSession(
                    session_id="scene-session",
                    last_event_ts=old + timedelta(minutes=20),
                    session_start_ts=old,
                    client_fingerprint="fp",
                    last_entity_type="scene",
                    last_entity_id=1,
                ),
                InteractionSession(
                    session_id="image-session",
                    last_event_ts=old + timedelta(minutes=15),
                    session_start_ts=old,
                    client_fingerprint="fp",
                    last_entity_type="image",
                    last_entity_id=2,
                ),
                InteractionSession(
                    session_id="short",
                    last_event_ts=old,
                    session_start_ts=old,
                    client_fingerprint="fp",
                ),
                InteractionSession(
                    session_id="new-scene",
                    last_event_ts=old + timedelta(minutes=12),
                    session_start_ts=old,
                    client_fingerprint="fp",
                    last_entity_type="scene",
                    last_entity_id=5,
                ),
                InteractionSession(
                    session_id="new-image",
                    last_event_ts=old + timedelta(minutes=12),
                    session_start_ts=old,
                    client_fingerprint="fp",
                    last_entity_type="image",
                    last_entity_id=6,
                ),
                SceneDerived(scene_id=1, view_count=2, derived_o_count=3),
                ImageDerived(image_id=2, view_count=1, derived_o_count=4),
            ]
        )
        db.commit()

        interactions._finalize_stale_sessions_for_fingerprint(
            db, "fp", datetime(2025, 1, 1)
        )
        db.commit()

        assert db.get(SceneDerived, 1).derived_o_count == 4
        assert db.get(ImageDerived, 2).derived_o_count == 5
        assert db.get(SceneDerived, 5).derived_o_count == 1
        assert db.get(SceneDerived, 5).view_count == 1
        assert db.get(ImageDerived, 6).derived_o_count == 1
        assert db.get(ImageDerived, 6).view_count == 1
        assert db.scalar(
            sa.select(InteractionSession.ended_at).where(
                InteractionSession.session_id == "scene-session"
            )
        ) is not None


def test_image_and_scene_derived_helpers_update_existing_and_create_missing(
    interaction_db,
):
    first = datetime(2024, 1, 1)
    second = datetime(2024, 1, 2)
    events = [
        event(
            "image-1",
            event_type="image_view",
            entity_type="image",
            entity_id=2,
            seconds=1,
        ),
        event(
            "image-2",
            event_type="image_view",
            entity_type="image",
            entity_id=2,
            seconds=2,
        ),
        event("scene-1", entity_id=3, seconds=1),
        event("scene-2", entity_id=3, seconds=2),
    ]
    events[0].ts = first
    events[1].ts = second
    events[2].ts = first
    events[3].ts = second

    with interaction_db() as db:
        db.add_all(
            [
                ImageDerived(image_id=2, view_count=5, derived_o_count=1),
                SceneDerived(scene_id=3, view_count=4, derived_o_count=1),
            ]
        )
        db.commit()

        interactions.update_image_derived(db, 2, events)
        interactions.update_image_derived(db, 9, [])
        interactions._bulk_update_scene_derived(db, events[2:], {3, 4})
        interactions._bulk_update_scene_derived(db, [], set())
        db.commit()

        assert db.get(ImageDerived, 2).view_count == 7
        assert db.get(ImageDerived, 2).last_viewed_at == second
        assert db.get(ImageDerived, 9).view_count == 0
        assert db.get(SceneDerived, 3).view_count == 6
        assert db.get(SceneDerived, 3).last_viewed_at == second
        assert db.get(SceneDerived, 4).view_count == 0


def test_update_scene_watch_stats_uses_event_duration_then_page_duration(
    interaction_db,
):
    with interaction_db() as db:
        session = InteractionSession(
            session_id="session",
            last_event_ts=datetime(2024, 1, 1),
            session_start_ts=datetime(2024, 1, 1),
        )
        watch = SceneWatch(
            session_id="session",
            scene_id=1,
            page_entered_at=datetime(2024, 1, 1),
            page_left_at=datetime(2024, 1, 1, 0, 1, 40),
        )
        db.add_all([session, watch])
        db.flush()
        db.add(
            InteractionEvent(
                client_event_id="duration",
                session_id="session",
                event_type="scene_watch_pause",
                entity_type="scene",
                entity_id=1,
                client_ts=datetime(2024, 1, 1),
                event_metadata={"duration": "50"},
            )
        )
        db.commit()

        segments = [
            SceneWatchSegment(
                scene_watch_id=watch.id,
                session_id="session",
                scene_id=1,
                start_s=0,
                end_s=25,
                watched_s=25,
            )
        ]
        interactions._update_scene_watch_stats(db, watch, segments)
        assert watch.total_watched_s == 25
        assert watch.watch_percent == 50

        db.execute(sa.delete(InteractionEvent))
        db.flush()
        interactions._update_scene_watch_stats(db, watch, segments)
        assert watch.watch_percent == 25


def test_ingest_events_builds_sessions_watches_segments_and_derived_rows(
    interaction_db,
):
    base = datetime(2024, 1, 1, 12)
    with interaction_db() as db:
        db.add(
            InteractionSession(
                session_id="session",
                last_event_ts=base,
                session_start_ts=base,
            )
        )
        db.commit()

        events = [
            event("enter", seconds=0, event_type="scene_page_enter"),
            event("view", seconds=1, event_type="scene_view"),
            event(
                "start",
                seconds=2,
                event_type="scene_watch_start",
                metadata={"position": 0, "duration": 40},
            ),
            event(
                "progress",
                seconds=3,
                event_type="scene_watch_progress",
                metadata={"position": 8, "duration": 40},
            ),
            event(
                "pause",
                seconds=4,
                event_type="scene_watch_pause",
                metadata={"position": 10, "duration": 40},
            ),
            event(
                "seek",
                seconds=5,
                event_type="scene_seek",
                metadata={"from": 10, "to": 20},
            ),
            event(
                "start-2",
                seconds=6,
                event_type="scene_watch_start",
                metadata={"position": 20, "duration": 40},
            ),
            event(
                "complete",
                seconds=7,
                event_type="scene_watch_complete",
                metadata={"position": 30, "duration": 40},
            ),
            event("leave", seconds=8, event_type="scene_page_leave"),
            event(
                "image",
                seconds=9,
                event_type="image_view",
                entity_type="image",
                entity_id=2,
            ),
            event(
                "search",
                seconds=10,
                event_type="library_search",
                entity_type="library",
                entity_id=3,
                metadata={"query": "null", "filters": {"tag": "null"}},
            ),
        ]

        accepted, duplicates, errors = interactions.ingest_events(db, events)

        assert accepted == len(events)
        assert duplicates == 0
        assert errors == []
        assert db.scalar(
            sa.select(sa.func.count()).select_from(InteractionEvent)
        ) == len(events) - 1
        watch = db.scalar(sa.select(SceneWatch))
        assert watch is not None
        assert watch.page_entered_at == base
        assert watch.page_left_at == base + timedelta(seconds=8)
        assert watch.total_watched_s == 20
        assert watch.watch_percent == 50
        segments = db.scalars(
            sa.select(SceneWatchSegment).order_by(SceneWatchSegment.start_s)
        ).all()
        assert [(segment.start_s, segment.end_s) for segment in segments] == [
            (0.0, 10.0),
            (20.0, 30.0),
        ]
        assert db.get(SceneDerived, 1).view_count == 1
        assert db.get(ImageDerived, 2).view_count == 1
        search = db.scalar(sa.select(InteractionLibrarySearch))
        assert search.query is None
        assert search.filters == {"tag": None}

        duplicate = event(
            "image",
            seconds=11,
            event_type="gallery_view",
            entity_type="gallery",
            entity_id=4,
        )
        accepted, duplicates, errors = interactions.ingest_events(db, [duplicate])
        assert (accepted, duplicates, errors) == (0, 1, [])


def test_process_helpers_ignore_empty_batches_and_capture_image_errors(monkeypatch):
    interactions._process_scene_summaries(object(), [], [])
    errors = []
    monkeypatch.setattr(
        interactions,
        "update_image_derived",
        lambda *args: (_ for _ in ()).throw(RuntimeError("failed")),
    )
    interactions._process_image_derived(
        object(),
        [SimpleNamespace(entity_type="image", entity_id=1)],
        errors,
    )
    assert errors == ["image summary 1: failed"]


def test_persist_library_search_events_is_best_effort(monkeypatch):
    added = []
    fake_db = SimpleNamespace(add=added.append)
    events = [
        SimpleNamespace(
            type="library_search",
            entity_type="library",
            entity_id=0,
            event_metadata={"library": "scenes"},
            metadata={"query": "term", "filters": {"tag": "null"}},
            session_id="session",
        ),
        SimpleNamespace(
            type="other",
            entity_type="other",
            entity_id=0,
            metadata={},
            session_id="session",
        ),
    ]

    interactions._persist_library_search_events(fake_db, events)

    assert len(added) == 1
    assert added[0].library == "scenes"
    assert added[0].query == "term"
    assert added[0].filters == {"tag": None}

    monkeypatch.setattr(interactions, "InteractionLibrarySearch", None)
    interactions._persist_library_search_events(fake_db, events)
    assert len(added) == 1
