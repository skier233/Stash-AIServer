from __future__ import annotations
from typing import Iterable, List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import select, delete
from datetime import datetime, timezone
import traceback
from app.models.interaction import InteractionEvent, InteractionSession, SceneWatchSegment, SceneDerived
from app.schemas.interaction import InteractionEventIn

# Simple in-place segment reconstruction per (session_id, scene_id)
# Based on primitive events sequence ordering by client_ts

def ingest_events(db: Session, events: Iterable[InteractionEventIn]) -> Tuple[int,int,list[str]]:
    accepted = 0
    duplicates = 0
    errors: List[str] = []
    # Sort by client timestamp for deterministic processing
    ev_list = sorted(list(events), key=lambda e: e.ts)

    def _to_naive_utc(dt: datetime | None) -> datetime | None:
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt
        return dt.astimezone(timezone.utc).replace(tzinfo=None)

    for ev in ev_list:
        # Dedup by client_event_id (ev.id)
        if ev.id:
            existing = db.execute(select(InteractionEvent.id).where(InteractionEvent.client_event_id==ev.id)).first()
            if existing:
                duplicates += 1
                continue
        try:
            # Use a nested transaction (savepoint) so a failing event doesn't roll back others
            with db.begin_nested():
                # Only include model-backed fields; frontend no longer sends page_url/user_agent/viewport/schema_version
                client_ts_val = _to_naive_utc(ev.ts)
                obj = InteractionEvent(
                    client_event_id=ev.id,
                    session_id=ev.session_id,
                    event_type=ev.type,
                    entity_type=ev.entity_type,
                    entity_id=ev.entity_id,
                    client_ts=client_ts_val,
                    event_metadata=ev.metadata,
                )
                db.add(obj)
                # Flush inside the nested transaction to surface issues
                db.flush()
                # Update session state only after successful flush
                _update_session(db, obj)
            accepted += 1
        except Exception as e:  # pragma: no cover (best-effort logging)
            tb = traceback.format_exc()
            errors.append(f'event={getattr(ev, "id", None)} session={getattr(ev, "session_id", None)} type={getattr(ev, "type", None)} err={e} trace={tb}')
    # Flush so they are queryable for aggregation
    db.flush()
    # Aggregate per session+scene touched in this batch: persist segments and update derived table
    touched = {(e.session_id, e.entity_id) for e in ev_list if e.entity_type=='scene'}
    for sid, scene_id in touched:
        try:
            segments = recompute_segments(db, sid, scene_id)
            # remove prior segments for this session+scene to avoid duplicates
            db.execute(
                delete(SceneWatchSegment).where(
                    SceneWatchSegment.session_id == sid,
                    SceneWatchSegment.scene_id == scene_id
                )
            )
            # persist segments
            for s in segments:
                db.add(s)
            # update derived
            update_scene_derived(db, scene_id, ev_list)
        except Exception as e:  # pragma: no cover
            errors.append(f'summary {sid}/{scene_id}: {e}')
    # Update image-derived entries for any images touched in this batch
    touched_images = { e.entity_id for e in ev_list if e.entity_type == 'image' }
    for img_id in touched_images:
        try:
            update_image_derived(db, img_id, ev_list)
        except Exception as e:
            errors.append(f'image summary {img_id}: {e}')
    db.commit()
    return accepted, duplicates, errors


def _update_session(db: Session, ev: InteractionEvent):
    sess = db.execute(select(InteractionSession).where(InteractionSession.session_id==ev.session_id)).scalar_one_or_none()
    now = datetime.utcnow()
    scene_related = ev.entity_type=='scene'
    # normalize event timestamps to naive UTC for comparison
    def _to_naive(dt: datetime | None) -> datetime | None:
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt
        return dt.astimezone(timezone.utc).replace(tzinfo=None)

    ev_client_ts = _to_naive(ev.client_ts)

    if not sess:
        sess = InteractionSession(session_id=ev.session_id, last_event_ts=ev_client_ts or now, session_start_ts=ev_client_ts or now)
        if scene_related:
            sess.last_scene_id = ev.entity_id
            sess.last_scene_event_ts = now
        db.add(sess)
    else:
        if ev_client_ts and (sess.last_event_ts is None or ev_client_ts > sess.last_event_ts):
            sess.last_event_ts = ev_client_ts
        if scene_related:
            sess.last_scene_id = ev.entity_id
            sess.last_scene_event_ts = ev_client_ts


def recompute_segments(db: Session, session_id: str, scene_id: str):
    rows = db.execute(select(InteractionEvent).where(
        InteractionEvent.session_id==session_id,
        InteractionEvent.entity_type=='scene',
        InteractionEvent.entity_id==scene_id
    ).order_by(InteractionEvent.client_ts.asc())).scalars().all()

    segments: list[tuple[float,float]] = []
    last_play_start_pos: float | None = None
    last_position: float | None = None

    def close_segment(end_pos: float):
        nonlocal last_play_start_pos, last_position
        if last_play_start_pos is None:
            return
        start_pos = last_play_start_pos
        if end_pos > start_pos:
            segments.append((start_pos, end_pos))
        last_play_start_pos = None
        last_position = end_pos

    for ev in rows:
        meta = ev.event_metadata or {}
        et = ev.event_type
        if et == 'scene_watch_start':
            last_play_start_pos = float(meta.get('position') if meta.get('position') is not None else (last_position or 0.0))
        elif et == 'scene_seek':
            to_pos = meta.get('to')
            if to_pos is not None:
                if last_play_start_pos is not None and last_position is not None:
                    close_segment(last_position)
                last_position = float(to_pos)
                if last_play_start_pos is not None:
                    last_play_start_pos = last_position
        elif et in ('scene_watch_pause','scene_watch_complete'):
            pos = meta.get('position')
            if pos is None and last_position is not None:
                pos = last_position
            if pos is None and last_play_start_pos is not None:
                pos = last_play_start_pos
            if pos is not None:
                close_segment(float(pos))
        elif et == 'scene_watch_progress':
            pos = meta.get('position')
            if pos is not None:
                last_position = float(pos)

    # Merge
    merged: list[tuple[float,float]] = []
    for seg in sorted(segments, key=lambda s: s[0]):
        if not merged:
            merged.append(list(seg))
            continue
        last = merged[-1]
        if seg[0] <= last[1] + 1.0:
            last[1] = max(last[1], seg[1])
        else:
            merged.append(list(seg))

    # Convert to SceneWatchSegment objects
    out = []
    for s in merged:
        start, end = float(s[0]), float(s[1])
        watched = max(0.0, end - start)
        out.append(SceneWatchSegment(session_id=session_id, scene_id=scene_id, start_s=start, end_s=end, watched_s=watched))
    return out

def update_scene_derived(db: Session, scene_id: str, ev_list: list):
    # Touch last_viewed_at and update view_count and derived_o_count.
    # view_count increments by number of scene_view events in ev_list for this scene.
    last_view = None
    view_events = 0
    for e in ev_list:
        if e.entity_id == scene_id and e.entity_type == 'scene' and e.type == 'scene_view':
            last_view = e.ts
            view_events += 1
    if last_view is None and view_events == 0:
        return
    existing = db.execute(select(SceneDerived).where(SceneDerived.scene_id==scene_id)).scalar_one_or_none()
    if existing:
        if last_view is not None:
            existing.last_viewed_at = last_view
        if view_events:
            existing.view_count = existing.view_count + view_events
    else:
        db.add(SceneDerived(scene_id=scene_id, last_viewed_at=last_view, derived_o_count=0, view_count=view_events))

    # Recompute derived_o_count: count sessions where this scene was the last_scene_id
    try:
        res = db.execute(select(InteractionSession).where(InteractionSession.last_scene_id==scene_id)).scalars().all()
        derived_o = len(res)
        existing = db.execute(select(SceneDerived).where(SceneDerived.scene_id==scene_id)).scalar_one_or_none()
        if existing:
            existing.derived_o_count = derived_o
    except Exception:
        # best-effort; don't fail ingestion on analytics recompute
        pass

    # image-derived is handled separately


def update_image_derived(db: Session, image_id: str, ev_list: list):
    from app.models.interaction import ImageDerived
    last_view = None
    view_events = 0
    # find view events for this image in the batch
    for e in ev_list:
        if e.entity_type == 'image' and e.entity_id == image_id and e.type == 'image_view':
            last_view = e.ts
            view_events += 1
    if view_events == 0:
        return
    existing = db.execute(select(ImageDerived).where(ImageDerived.image_id==image_id)).scalar_one_or_none()
    if existing:
        if last_view is not None:
            existing.last_viewed_at = last_view
        existing.view_count = existing.view_count + view_events
    else:
        db.add(ImageDerived(image_id=image_id, last_viewed_at=last_view, derived_o_count=0, view_count=view_events))
