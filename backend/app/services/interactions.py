from __future__ import annotations
from typing import Iterable, List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import select, delete
from datetime import datetime, timezone, timedelta
import traceback
from app.models.interaction import InteractionEvent, InteractionSession, SceneWatch, SceneWatchSegment, SceneDerived
from app.schemas.interaction import InteractionEventIn

# Simple in-place segment reconstruction per (session_id, scene_id)
# Based on primitive events sequence ordering by client_ts

def ingest_events(db: Session, events: Iterable[InteractionEventIn], client_fingerprint: str | None = None) -> Tuple[int,int,list[str]]:
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
        # determine canonical session first (so stored events and summaries use same session id)
        try:
            client_ts_val = _to_naive_utc(ev.ts)
            # find or create canonical session for this event
            sess = _find_or_create_session(db, ev.session_id, client_fingerprint, client_ts_val, ev.entity_type == 'scene', ev.entity_id)
            # set the event's session_id to the canonical session id so we store under that session
            ev.session_id = sess.session_id
        except Exception as e:
            tb = traceback.format_exc()
            errors.append(f'event={getattr(ev, "id", None)} session={getattr(ev, "session_id", None)} type={getattr(ev, "type", None)} err={e} trace={tb}')
            continue

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
                # Update session state only after successful flush (no merging here)
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
            # Find or create scene watch record for this session+scene
            scene_watch = _find_or_create_scene_watch(db, sid, scene_id, ev_list)
            segments = recompute_segments(db, sid, scene_id, scene_watch.id)
            # remove prior segments for this session+scene to avoid duplicates
            db.execute(
                delete(SceneWatchSegment).where(
                    SceneWatchSegment.session_id == sid,
                    SceneWatchSegment.scene_id == scene_id
                )
            )
            # persist segments with scene_watch_id reference
            for s in segments:
                db.add(s)
            # update scene watch stats
            _update_scene_watch_stats(db, scene_watch, segments)
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
        # If there is no session row for this session_id, create a new one (merging handled earlier)
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


def _find_or_create_session(db: Session, incoming_session_id: str, client_fingerprint: str | None, ev_client_ts: datetime | None, scene_related: bool, entity_id: str | None):
    """Return an InteractionSession object to use for storing an incoming event.
    This function prefers merging only on client_fingerprint (if provided). It will
    return an existing recent session or create a new session with the incoming
    session id and client_fingerprint attached.
    """
    now = datetime.utcnow()
    time_threshold = now - timedelta(minutes=2)
    # Try exact session id first
    sess = db.execute(select(InteractionSession).where(InteractionSession.session_id==incoming_session_id)).scalar_one_or_none()
    if sess:
        return sess

    # Prefer merging only on client_fingerprint to avoid cross-device merges via IP
    if client_fingerprint:
        try:
            merged = db.execute(select(InteractionSession).where(
                InteractionSession.client_fingerprint == client_fingerprint,
                InteractionSession.last_event_ts >= time_threshold
            ).order_by(InteractionSession.last_event_ts.desc())).scalar_one_or_none()
            if merged:
                return merged
        except Exception:
            pass

    # No merge found: create new session row with the incoming session id and fingerprint (if present)
    new_sess = InteractionSession(session_id=incoming_session_id, last_event_ts=ev_client_ts or now, session_start_ts=ev_client_ts or now, client_fingerprint=client_fingerprint)
    if scene_related and entity_id is not None:
        new_sess.last_scene_id = entity_id
        new_sess.last_scene_event_ts = now
    db.add(new_sess)
    # flush so the session is visible in subsequent selects within this transaction
    db.flush()
    return new_sess


def recompute_segments(db: Session, session_id: str, scene_id: str, scene_watch_id: int):
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
        out.append(SceneWatchSegment(scene_watch_id=scene_watch_id, session_id=session_id, scene_id=scene_id, start_s=start, end_s=end, watched_s=watched))
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

    # Ensure a SceneDerived row exists so we can persist derived_o_count even when
    # there are no explicit scene_view events in the batch (e.g. only scene_watch events).
    existing = db.execute(select(SceneDerived).where(SceneDerived.scene_id==scene_id)).scalar_one_or_none()
    if existing:
        if last_view is not None:
            existing.last_viewed_at = last_view
        if view_events:
            existing.view_count = existing.view_count + view_events
    else:
        # create with whatever view_events we have (may be zero)
        db.add(SceneDerived(scene_id=scene_id, last_viewed_at=last_view, derived_o_count=0, view_count=view_events))

    # Recompute derived_o_count using a union of signals so flaky timestamps
    # or reordering won't prevent expected updates. We consider a session
    # "qualified" if ANY of the following hold:
    #  - InteractionSession where this scene is last_scene_id and session duration >= threshold
    #  - SceneWatch for this scene where page_left_at - page_entered_at >= threshold
    #  - SceneWatch where total_watched_s >= threshold
    try:
        import os
        min_session_minutes = int(os.getenv('INTERACTION_MIN_SESSION_MINUTES', '10'))
        min_session_seconds = min_session_minutes * 60

        qualified_session_ids: set[str] = set()

        # 1) Sessions where this scene was last_scene and session duration >= threshold
        sessions = db.execute(select(InteractionSession).where(
            InteractionSession.last_scene_id == scene_id
        )).scalars().all()
        for sess in sessions:
            try:
                if sess.last_event_ts and sess.session_start_ts:
                    duration = (sess.last_event_ts - sess.session_start_ts).total_seconds()
                    if duration >= min_session_seconds:
                        qualified_session_ids.add(sess.session_id)
            except Exception:
                # ignore malformed timestamps for a best-effort count
                continue

        # 2) SceneWatch rows where page duration meets threshold
        try:
            from app.models.interaction import SceneWatch
            watches = db.execute(select(SceneWatch).where(SceneWatch.scene_id == scene_id)).scalars().all()
            for w in watches:
                if w.page_entered_at and w.page_left_at:
                    try:
                        dur = (w.page_left_at - w.page_entered_at).total_seconds()
                        if dur >= min_session_seconds:
                            qualified_session_ids.add(w.session_id)
                    except Exception:
                        pass
                # also consider total_watched_s as qualification
                try:
                    if getattr(w, 'total_watched_s', 0) and w.total_watched_s >= float(min_session_seconds):
                        qualified_session_ids.add(w.session_id)
                except Exception:
                    pass
        except Exception:
            # If SceneWatch model/table isn't present or query fails, ignore
            pass

        # Persist the computed derived_o_count
        existing = db.execute(select(SceneDerived).where(SceneDerived.scene_id==scene_id)).scalar_one_or_none()
        if existing:
            existing.derived_o_count = len(qualified_session_ids)
    except Exception:
        # best-effort; don't fail ingestion on analytics recompute
        pass

    # image-derived is handled separately


def _find_or_create_scene_watch(db: Session, session_id: str, scene_id: str, ev_list: list) -> SceneWatch:
    """Find existing scene watch record or create one based on scene_page_enter/scene_view events"""
    # Look for existing scene watch record
    existing = db.execute(select(SceneWatch).where(
        SceneWatch.session_id == session_id,
        SceneWatch.scene_id == scene_id
    )).scalar_one_or_none()
    
    if existing:
        # Update page_left_at if we have scene_page_leave events
        for e in ev_list:
            if e.entity_id == scene_id and e.entity_type == 'scene' and e.type == 'scene_page_leave':
                existing.page_left_at = e.ts
        return existing
    
    # Create new scene watch record
    page_entered_at = None
    page_left_at = None
    
    # Find page enter/leave events
    for e in ev_list:
        if e.entity_id == scene_id and e.entity_type == 'scene':
            if e.type == 'scene_page_enter':
                page_entered_at = e.ts
            elif e.type == 'scene_page_leave':
                page_left_at = e.ts
            elif e.type == 'scene_view' and page_entered_at is None:
                # Fallback: use scene_view as page enter if no explicit page_enter event
                page_entered_at = e.ts
    
    if page_entered_at is None:
        # Default to earliest event timestamp for this scene
        scene_events = [e for e in ev_list if e.entity_id == scene_id and e.entity_type == 'scene']
        if scene_events:
            page_entered_at = min(scene_events, key=lambda x: x.ts).ts
        else:
            page_entered_at = datetime.utcnow()
    
    new_watch = SceneWatch(
        session_id=session_id,
        scene_id=scene_id,
        page_entered_at=page_entered_at,
        page_left_at=page_left_at
    )
    db.add(new_watch)
    db.flush()
    return new_watch


def _update_scene_watch_stats(db: Session, scene_watch: SceneWatch, segments: list[SceneWatchSegment]):
    """Update scene watch statistics based on computed segments"""
    total_watched = sum(seg.watched_s for seg in segments)
    scene_watch.total_watched_s = total_watched
    
    # Try to compute watch percentage if we can determine video duration
    # This could be enhanced to query scene metadata or use duration from events
    try:
        # Look for duration in recent scene_watch_complete events
        duration_events = db.execute(select(InteractionEvent).where(
            InteractionEvent.session_id == scene_watch.session_id,
            InteractionEvent.entity_type == 'scene',
            InteractionEvent.entity_id == scene_watch.scene_id,
            InteractionEvent.event_type == 'scene_watch_complete'
        ).order_by(InteractionEvent.client_ts.desc()).limit(1)).scalars().all()
        
        for ev in duration_events:
            meta = ev.event_metadata or {}
            duration = meta.get('duration')
            if duration and duration > 0:
                scene_watch.watch_percent = min(100.0, (total_watched / duration) * 100.0)
                break
    except Exception:
        pass


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
