from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select

from stash_ai_server.db.session import SessionLocal
from stash_ai_server.models.interaction import (
    InteractionEvent,
    InteractionSession,
    SceneWatch,
    SceneWatchSegment,
)
from stash_ai_server.schemas.interaction import InteractionEventIn
from stash_ai_server.services.interactions import ingest_events


@pytest.mark.integration
@pytest.mark.parametrize("progress_events, include_complete", [(10, True), (10, False)])
def test_long_watch_segment_spanning_batches(progress_events: int, include_complete: bool):
    """Ensure long viewing sessions split across batches persist a continuous segment even without an explicit pause."""
    session = SessionLocal()
    scene_id = 424242
    session_id = "session-long-window"
    client_id = "client-long-window"
    fingerprint = "fingerprint-long-window"

    base_ts = datetime.utcnow().replace(microsecond=0)

    def make_event(suffix: str, offset_seconds: float, event_type: str, metadata: dict | None = None) -> InteractionEventIn:
        return InteractionEventIn(
            id=f"{session_id}-{suffix}",
            session_id=session_id,
            client_id=client_id,
            ts=base_ts + timedelta(seconds=offset_seconds),
            type=event_type,
            entity_type="scene",
            entity_id=scene_id,
            metadata=metadata or {},
        )

    try:
        # Clean previous data for this synthetic session
        session.execute(delete(SceneWatchSegment).where(SceneWatchSegment.session_id == session_id, SceneWatchSegment.scene_id == scene_id))
        session.execute(delete(SceneWatch).where(SceneWatch.session_id == session_id, SceneWatch.scene_id == scene_id))
        session.execute(delete(InteractionEvent).where(InteractionEvent.session_id == session_id, InteractionEvent.entity_id == scene_id))
        session.execute(delete(InteractionSession).where(InteractionSession.session_id == session_id))
        session.commit()

        # First batch: enter + start + many progress updates to push the start event outside the 5-row history window
        base_events: list[InteractionEventIn] = [
            make_event("enter", 0, "scene_page_enter"),
            make_event("start", 1, "scene_watch_start", {"position": 0, "duration": 180}),
        ]
        for idx in range(progress_events):
            position = (idx + 1) * 5
            base_events.append(
                make_event(
                    f"progress-{idx}",
                    offset_seconds=2 + position,
                    event_type="scene_watch_progress",
                    metadata={"position": float(position)},
                )
            )

        ingest_events(session, base_events, client_fingerprint=fingerprint)

        # Second batch arrives later with additional progress updates and optionally the completion event
        expected_end = 125.0 if include_complete else 120.0
        trailing_events = [
            make_event(
                "progress-tail",
                offset_seconds=120,
                event_type="scene_watch_progress",
                metadata={"position": 120.0},
            ),
        ]
        if include_complete:
            trailing_events.append(
                make_event(
                    "complete",
                    offset_seconds=125,
                    event_type="scene_watch_complete",
                    metadata={"position": expected_end, "duration": 180},
                )
            )
        else:
            trailing_events.append(
                make_event(
                    "leave",
                    offset_seconds=125,
                    event_type="scene_page_leave",
                    metadata={},
                )
            )

        ingest_events(session, trailing_events, client_fingerprint=fingerprint)

        # Fetch the resulting segments
        segments = session.execute(
            select(SceneWatchSegment).where(
                SceneWatchSegment.session_id == session_id,
                SceneWatchSegment.scene_id == scene_id,
            )
        ).scalars().all()

        assert len(segments) == 1
        segment = segments[0]
        assert segment.start_s == pytest.approx(0.0)
        assert segment.end_s == pytest.approx(expected_end)
        assert segment.watched_s == pytest.approx(expected_end)

        watch = session.execute(
            select(SceneWatch).where(
                SceneWatch.session_id == session_id,
                SceneWatch.scene_id == scene_id,
            )
        ).scalars().first()
        assert watch is not None
        assert watch.total_watched_s == pytest.approx(expected_end)
        assert watch.watch_percent is None or watch.watch_percent >= 0
    finally:
        session.execute(delete(SceneWatchSegment).where(SceneWatchSegment.session_id == session_id, SceneWatchSegment.scene_id == scene_id))
        session.execute(delete(SceneWatch).where(SceneWatch.session_id == session_id, SceneWatch.scene_id == scene_id))
        session.execute(delete(InteractionEvent).where(InteractionEvent.session_id == session_id, InteractionEvent.entity_id == scene_id))
        session.execute(delete(InteractionSession).where(InteractionSession.session_id == session_id))
        session.commit()
        session.close()


@pytest.mark.integration
@pytest.mark.parametrize(
    "progress_position, expected_segments, expected_watch",
    [
        (1.0, 0, 0.0),
        (2.0, 1, 2.0),
    ],
)
def test_segments_below_min_threshold_are_discarded(progress_position: float, expected_segments: int, expected_watch: float):
    session = SessionLocal()
    scene_id = f"scene-threshold-{progress_position}"
    session_id = f"session-threshold-{progress_position}"
    client_id = "client-threshold"
    fingerprint = "fingerprint-threshold"

    base_ts = datetime.utcnow().replace(microsecond=0)

    def make_event(suffix: str, offset_seconds: float, event_type: str, metadata: dict | None = None) -> InteractionEventIn:
        return InteractionEventIn(
            id=f"{session_id}-{suffix}",
            session_id=session_id,
            client_id=client_id,
            ts=base_ts + timedelta(seconds=offset_seconds),
            type=event_type,
            entity_type="scene",
            entity_id=scene_id,
            metadata=metadata or {},
        )

    try:
        session.execute(delete(SceneWatchSegment).where(SceneWatchSegment.session_id == session_id, SceneWatchSegment.scene_id == scene_id))
        session.execute(delete(SceneWatch).where(SceneWatch.session_id == session_id, SceneWatch.scene_id == scene_id))
        session.execute(delete(InteractionEvent).where(InteractionEvent.session_id == session_id, InteractionEvent.entity_id == scene_id))
        session.execute(delete(InteractionSession).where(InteractionSession.session_id == session_id))
        session.commit()

        ingest_events(
            session,
            [
                make_event("enter", 0, "scene_page_enter"),
                make_event("start", 0.5, "scene_watch_start", {"position": 0.0, "duration": 30}),
                make_event("progress", 0.5 + progress_position, "scene_watch_progress", {"position": progress_position}),
                make_event("leave", 1.0 + progress_position, "scene_page_leave"),
            ],
            client_fingerprint=fingerprint,
        )

        segments = session.execute(
            select(SceneWatchSegment).where(
                SceneWatchSegment.session_id == session_id,
                SceneWatchSegment.scene_id == scene_id,
            )
        ).scalars().all()

        assert len(segments) == expected_segments
        if expected_segments:
            seg = segments[0]
            assert seg.start_s == pytest.approx(0.0)
            assert seg.end_s == pytest.approx(expected_watch)
            assert seg.watched_s == pytest.approx(expected_watch)

        watch = session.execute(
            select(SceneWatch).where(
                SceneWatch.session_id == session_id,
                SceneWatch.scene_id == scene_id,
            )
        ).scalars().first()
        assert watch is not None
        assert watch.total_watched_s == pytest.approx(expected_watch)
    finally:
        session.execute(delete(SceneWatchSegment).where(SceneWatchSegment.session_id == session_id, SceneWatchSegment.scene_id == scene_id))
        session.execute(delete(SceneWatch).where(SceneWatch.session_id == session_id, SceneWatch.scene_id == scene_id))
        session.execute(delete(InteractionEvent).where(InteractionEvent.session_id == session_id, InteractionEvent.entity_id == scene_id))
        session.execute(delete(InteractionSession).where(InteractionSession.session_id == session_id))
        session.commit()
        session.close()
