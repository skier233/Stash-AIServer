from __future__ import annotations
from typing import Iterable, List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import select, delete
from datetime import datetime, timezone, timedelta
import traceback
from app.models.interaction import InteractionEvent, InteractionSession, SceneWatch, SceneWatchSegment, SceneDerived, InteractionSessionAlias
from sqlalchemy.exc import IntegrityError
import os
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
            sess_id = _find_or_create_session_id(db, ev.session_id, client_fingerprint)
            # set the event's session_id to the canonical session id so we store under that session
            ev.session_id = sess_id
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
    # Aggregate & derived updates split into helper functions for readability
    _process_scene_summaries(db, ev_list, errors)
    _process_image_derived(db, ev_list, errors)
    _persist_library_search_events(db, ev_list)
    db.commit()
    return accepted, duplicates, errors


def _update_session(db: Session, ev: InteractionEvent):
    sess = db.execute(select(InteractionSession).where(InteractionSession.session_id==ev.session_id)).scalar_one_or_none()
    # scene_related variable removed (unused)
    # normalize event timestamps to naive UTC for comparison
    def _to_naive(dt: datetime | None) -> datetime | None:
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt
        return dt.astimezone(timezone.utc).replace(tzinfo=None)

    ev_client_ts = _to_naive(ev.client_ts)

    if not sess:
        raise ValueError(f'session not found for event {ev.id} session_id={ev.session_id}')

    if ev_client_ts and (sess.last_event_ts is None or ev_client_ts > sess.last_event_ts):
        sess.last_event_ts = ev_client_ts
    # scene-related context is recorded via generic last_entity_* fields below
    # Update generic last-entity for relevant event types
    try:
        if ev.entity_type in ('scene','image','gallery'):
            sess.last_entity_type = ev.entity_type
            sess.last_entity_id = ev.entity_id
            sess.last_entity_event_ts = ev_client_ts
    except Exception:
        pass
    # Special-case: session_end may carry last_entity metadata with the final viewed item
    try:
        if ev.entity_type == 'session':
            meta = ev.event_metadata or {}
            last_ent = meta.get('last_entity')
            if last_ent and isinstance(last_ent, dict):
                t = last_ent.get('type')
                i = last_ent.get('id')
                ts = last_ent.get('ts')
                if t and i:
                    try:
                        sess.last_entity_type = t
                        sess.last_entity_id = str(i)
                        # prefer event-provided ts if parseable
                        if ts:
                            try:
                                # Try builtin ISO parser first
                                parsed = datetime.fromisoformat(ts)
                                # strip tzinfo if present
                                sess.last_entity_event_ts = parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
                            except Exception:
                                try:
                                    # Fallback: treat ts as epoch ms if numeric
                                    if str(ts).isdigit():
                                        sess.last_entity_event_ts = datetime.utcfromtimestamp(int(ts) / 1000.0)
                                    else:
                                        sess.last_entity_event_ts = ev_client_ts or datetime.now(timezone.utc)
                                except Exception:
                                    sess.last_entity_event_ts = ev_client_ts or datetime.now(timezone.utc)
                    except Exception:
                        pass
    except Exception:
        pass


def _find_or_create_session_id(db: Session, incoming_session_id: str, client_fingerprint: str | None):
    """Resolve incoming_session_id to a canonical session_id string.

    Updated strategy (no static alias expiration):
    - If a session row with incoming_session_id exists, treat it as canonical and return it.
    - Else, if an alias maps this incoming id to a canonical session whose last_event_ts is still
      within the merge window, return the canonical id (dynamic recency check).
    - Else, if client_fingerprint is provided, try to find the most recent qualifying session for
      that fingerprint (last_event_ts within merge window) and (if found) create a persistent alias
      from the incoming id -> canonical id (no need to refresh / extend) and return canonical id.
    - Otherwise create a brand new session row with incoming_session_id and return it.

    Aliases are durable pointers; their usefulness is gated by the recency of the canonical session.
    """

    # 1) Direct session id exists -> canonical
    direct = db.execute(select(InteractionSession.session_id).where(InteractionSession.session_id == incoming_session_id)).first()
    if direct:
        return incoming_session_id

    now = datetime.now(timezone.utc)
    merge_ttl_seconds = int(os.getenv('INTERACTION_MERGE_TTL_SECONDS', '120'))
    time_threshold = now - timedelta(seconds=merge_ttl_seconds)

    # 2) Alias mapping exists (no recency filter) -> return canonical pointed id
    try:
        alias_row = db.execute(
            select(InteractionSessionAlias.canonical_session_id).where(
                InteractionSessionAlias.alias_session_id == incoming_session_id
            )
        ).first()
        if alias_row:
            return alias_row[0]
    except Exception:
        # alias table might not exist yet; ignore
        pass

    # 3) Fingerprint merge: recent session for same fingerprint
    if client_fingerprint:
        try:
            recent = db.execute(
                select(InteractionSession.session_id).where(
                    InteractionSession.client_fingerprint == client_fingerprint,
                    InteractionSession.last_event_ts >= time_threshold
                ).order_by(InteractionSession.last_event_ts.desc()).limit(1)
            ).first()
            if recent:
                canonical_id = recent[0]
                if canonical_id != incoming_session_id:
                    try:
                        alias_kwargs = {"alias_session_id": incoming_session_id, "canonical_session_id": canonical_id}
                        db.add(InteractionSessionAlias(**alias_kwargs))
                        try:
                            db.flush()
                        except IntegrityError:
                            try:
                                db.rollback()
                            except Exception:
                                pass
                    except Exception:
                        pass
                return canonical_id
        except Exception:
            pass

    # 4) Create new session with incoming id
    try:
        db.add(InteractionSession(session_id=incoming_session_id, last_event_ts=now, session_start_ts=now, client_fingerprint=client_fingerprint))
        db.flush()
        return incoming_session_id
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        # Race: someone created it meanwhile
        direct = db.execute(select(InteractionSession.session_id).where(InteractionSession.session_id == incoming_session_id)).first()
        if direct:
            return direct[0]
        raise


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
            # Frontend now guarantees metadata { from, to } with accurate pre/post positions.
            was_playing = last_play_start_pos is not None
            from_pos = meta.get('from')
            to_pos = meta.get('to')
            try:
                if was_playing:
                    # Close existing playing segment at "from" (or last_position fallback)
                    end_pos = float(from_pos) if from_pos is not None else (last_position if last_position is not None else last_play_start_pos)
                    close_segment(end_pos)
                if to_pos is not None:
                    new_pos = float(to_pos)
                    last_position = new_pos
                    # Resume playing continuity only if we were playing before the seek
                    if was_playing:
                        last_play_start_pos = new_pos
                    else:
                        # If not previously playing, don't start a new segment automatically
                        last_play_start_pos = None
                else:
                    # Missing destination: just drop play state to avoid corrupt spans
                    last_play_start_pos = None
            except Exception:
                # On malformed metadata, stop current segment safely
                if was_playing:
                    close_segment(last_position if last_position is not None else (last_play_start_pos or 0.0))
                last_play_start_pos = None
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

        # 1) Sessions where this scene was the last entity in the session and session duration >= threshold
        sessions = db.execute(select(InteractionSession).where(
            (InteractionSession.last_entity_type == 'scene') & (InteractionSession.last_entity_id == scene_id)
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
        # If still missing, try to infer from session metadata (session end / last entity ts)
        if existing.page_left_at is None:
            try:
                sess = db.execute(select(InteractionSession).where(InteractionSession.session_id==session_id)).scalar_one_or_none()
                if sess:
                    if getattr(sess, 'last_entity_type', None) == 'scene' and getattr(sess, 'last_entity_id', None) == scene_id and getattr(sess, 'last_entity_event_ts', None):
                        existing.page_left_at = sess.last_entity_event_ts
                    elif getattr(sess, 'last_event_ts', None):
                        existing.page_left_at = sess.last_event_ts
            except Exception:
                pass
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
            page_entered_at = datetime.now(timezone.utc)
    # Try to infer page_left_at from session metadata if not present in events
    if page_left_at is None:
        try:
            sess = db.execute(select(InteractionSession).where(InteractionSession.session_id==session_id)).scalar_one_or_none()
            if sess:
                if getattr(sess, 'last_entity_type', None) == 'scene' and getattr(sess, 'last_entity_id', None) == scene_id and getattr(sess, 'last_entity_event_ts', None):
                    page_left_at = sess.last_entity_event_ts
                elif getattr(sess, 'last_event_ts', None):
                    page_left_at = sess.last_event_ts
        except Exception:
            pass
    # Final fallback: use latest scene event timestamp in the batch
    if page_left_at is None:
        scene_events = [e for e in ev_list if e.entity_id == scene_id and e.entity_type == 'scene']
        if scene_events:
            page_left_at = max(scene_events, key=lambda x: x.ts).ts
    
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
    # First try explicit duration from scene_watch_complete events
    duration = None
    try:
        duration_events = db.execute(select(InteractionEvent).where(
            InteractionEvent.session_id == scene_watch.session_id,
            InteractionEvent.entity_type == 'scene',
            InteractionEvent.entity_id == scene_watch.scene_id,
            InteractionEvent.event_type == 'scene_watch_complete'
        ).order_by(InteractionEvent.client_ts.desc()).limit(1)).scalars().all()
        for ev in duration_events:
            meta = ev.event_metadata or {}
            d = meta.get('duration')
            try:
                if d is not None and float(d) > 0:
                    duration = float(d)
                    break
            except Exception:
                continue
    except Exception:
        duration = None

    # If we couldn't find an explicit duration, try to infer from page_entered/page_left
    if duration is None:
        try:
            if scene_watch.page_entered_at and scene_watch.page_left_at:
                dur = (scene_watch.page_left_at - scene_watch.page_entered_at).total_seconds()
                if dur > 0:
                    duration = dur
        except Exception:
            duration = None

    # Only set watch_percent if we have a reliable duration (from complete event or page enter/leave)
    if duration and duration > 0:
        try:
            scene_watch.watch_percent = min(100.0, (total_watched / float(duration)) * 100.0)
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
        # Even if there are no explicit image_view events in this batch, we may
        # still need to recompute derived_o_count based on session last-entity.
        pass
    existing = db.execute(select(ImageDerived).where(ImageDerived.image_id==image_id)).scalar_one_or_none()
    if existing:
        if last_view is not None:
            existing.last_viewed_at = last_view
        existing.view_count = existing.view_count + view_events
    else:
        db.add(ImageDerived(image_id=image_id, last_viewed_at=last_view, derived_o_count=0, view_count=view_events))

    # Recompute derived_o_count for images: count sessions where this image is the
    # last_entity and session duration >= threshold
    try:
        import os
        min_session_minutes = int(os.getenv('INTERACTION_MIN_SESSION_MINUTES', '10'))
        min_session_seconds = min_session_minutes * 60
        sessions = db.execute(select(InteractionSession).where(
            (InteractionSession.last_entity_type == 'image') & (InteractionSession.last_entity_id == image_id)
        )).scalars().all()
        qualified = 0
        for s in sessions:
            try:
                if s.last_event_ts and s.session_start_ts:
                    duration = (s.last_event_ts - s.session_start_ts).total_seconds()
                    if duration >= min_session_seconds:
                        qualified += 1
            except Exception:
                continue
        existing = db.execute(select(ImageDerived).where(ImageDerived.image_id==image_id)).scalar_one_or_none()
        if existing:
            existing.derived_o_count = qualified
    except Exception:
        pass


# -------------------------- Helper aggregation sections --------------------------
def _process_scene_summaries(db: Session, ev_list: list, errors: list[str]):
    """Efficiently aggregate per (session, scene) without O(N*M) rescans.

    Strategy:
      1. Pre-group scene events by (session_id, scene_id).
      2. Bulk load existing SceneWatch rows & InteractionSession rows for touched sessions.
      3. For each pair build / update SceneWatch (compute page_entered/left from its own events only).
      4. Recompute segments only if this batch had watch-related events for the pair.
      5. Update stats + derived counts.

    Behavior differences vs previous inline approach (intentional optimizations):
      - We no longer rescan the entire batch for each pair; only its own scene events.
      - Segments are recomputed only if watch/seek/progress events appear in the batch for that pair; avoids redundant writes.
      - Page leave inference still falls back to session metadata if not present in events.
    """
    from collections import defaultdict
    # 1. Group scene events
    scene_events_by_pair = defaultdict(list)
    for ev in ev_list:
        if getattr(ev, 'entity_type', None) == 'scene' and ev.entity_id:
            scene_events_by_pair[(ev.session_id, ev.entity_id)].append(ev)
    if not scene_events_by_pair:
        return

    session_ids = {sid for (sid, _) in scene_events_by_pair.keys()}
    scene_ids = {scene_id for (_, scene_id) in scene_events_by_pair.keys()}

    # 2. Bulk fetch existing watches
    existing_watches = db.execute(
        select(SceneWatch).where(
            SceneWatch.session_id.in_(session_ids),
            SceneWatch.scene_id.in_(scene_ids)
        )
    ).scalars().all()
    watch_map = {(w.session_id, w.scene_id): w for w in existing_watches}

    # Bulk fetch sessions for fallback inference
    sessions = db.execute(select(InteractionSession).where(InteractionSession.session_id.in_(session_ids))).scalars().all()
    session_map = {s.session_id: s for s in sessions}

    watch_related_types = {
        'scene_watch_start', 'scene_watch_pause', 'scene_watch_complete',
        'scene_watch_progress', 'scene_seek'
    }

    new_watches = []
    timing_changed: dict[tuple[str,str], bool] = {}

    # 3. Build / update SceneWatch rows
    for (sid, scene_id), sc_events in scene_events_by_pair.items():
        try:
            watch = watch_map.get((sid, scene_id))
            if watch:
                changed = False
                # Preserve earliest enter; only update if we don't have one or found an earlier
                for ev in sc_events:
                    if ev.type == 'scene_page_enter':
                        if watch.page_entered_at is None or ev.ts < watch.page_entered_at:
                            watch.page_entered_at = ev.ts
                            changed = True
                    elif ev.type == 'scene_page_leave':
                        # Only set/extend leave if it's truly later (avoid overwriting with earlier leaves)
                        if watch.page_left_at is None or ev.ts > watch.page_left_at:
                            watch.page_left_at = ev.ts
                            changed = True
                # Session fallback ONLY if user navigated away from this scene (different last_entity)
                if watch.page_left_at is None:
                    sess = session_map.get(sid)
                    if sess:
                        try:
                            if (getattr(sess, 'last_entity_type', None) != 'scene' or getattr(sess, 'last_entity_id', None) != scene_id):
                                cand = getattr(sess, 'last_entity_event_ts', None) or getattr(sess, 'last_event_ts', None)
                                if cand and (watch.page_entered_at is None or cand >= watch.page_entered_at):
                                    watch.page_left_at = cand
                                    changed = True
                        except Exception:
                            pass
                if changed:
                    timing_changed[(sid, scene_id)] = True
            else:
                page_entered_at = None
                page_left_at = None
                changed = False
                for ev in sc_events:
                    if ev.type == 'scene_page_enter':
                        page_entered_at = ev.ts
                    elif ev.type == 'scene_page_leave':
                        page_left_at = ev.ts
                    elif ev.type == 'scene_view' and page_entered_at is None:
                        page_entered_at = ev.ts
                if page_entered_at is None:
                    page_entered_at = min(sc_events, key=lambda x: x.ts).ts
                # Infer leave ONLY if user clearly navigated away
                if page_left_at is None:
                    sess = session_map.get(sid)
                    if sess:
                        try:
                            if (getattr(sess, 'last_entity_type', None) != 'scene' or getattr(sess, 'last_entity_id', None) != scene_id):
                                cand = getattr(sess, 'last_entity_event_ts', None) or getattr(sess, 'last_event_ts', None)
                                if cand and cand >= page_entered_at:
                                    page_left_at = cand
                        except Exception:
                            pass
                # If still None, we keep it None (page considered active)
                watch = SceneWatch(
                    session_id=sid,
                    scene_id=scene_id,
                    page_entered_at=page_entered_at,
                    page_left_at=page_left_at
                )
                db.add(watch)
                new_watches.append(watch)
                watch_map[(sid, scene_id)] = watch
                timing_changed[(sid, scene_id)] = True
        except Exception as e:  # pragma: no cover
            errors.append(f'scene_watch {sid}/{scene_id}: {e}')

    if new_watches:
        try:
            db.flush()
        except Exception as e:  # pragma: no cover
            errors.append(f'scene_watch_flush: {e}')

    # 4 & 5. Segments & derived updates
    for (sid, scene_id), sc_events in scene_events_by_pair.items():
        watch = watch_map.get((sid, scene_id))
        if not watch:
            continue
        try:
            if timing_changed.get((sid, scene_id)) or any(ev.type in watch_related_types for ev in sc_events):
                segments = recompute_segments(db, sid, scene_id, watch.id)
                db.execute(
                    delete(SceneWatchSegment).where(
                        SceneWatchSegment.session_id == sid,
                        SceneWatchSegment.scene_id == scene_id
                    )
                )
                for s in segments:
                    db.add(s)
                _update_scene_watch_stats(db, watch, segments)
            update_scene_derived(db, scene_id, ev_list)
        except Exception as e:  # pragma: no cover
            errors.append(f'summary {sid}/{scene_id}: {e}')


def _process_image_derived(db: Session, ev_list: list, errors: list[str]):
    """Update image-derived rows for images touched in this batch."""
    touched_images = {e.entity_id for e in ev_list if getattr(e, 'entity_type', None) == 'image'}
    for img_id in touched_images:
        try:
            update_image_derived(db, img_id, ev_list)
        except Exception as e:  # pragma: no cover
            errors.append(f'image summary {img_id}: {e}')


def _persist_library_search_events(db: Session, ev_list: list):
    """Persist library search events for analytics (best-effort)."""
    try:
        from app.models.interaction import InteractionLibrarySearch
        for e in ev_list:
            if getattr(e, 'type', None) == 'library_search' or getattr(e, 'entity_type', None) == 'library':
                lib = None
                try:
                    lib = e.entity_id if e.entity_id else (getattr(e, 'event_metadata', None) or {}).get('library')
                except Exception:
                    pass
                if not lib:
                    continue
                meta = getattr(e, 'metadata', None) or {}
                q = meta.get('query')
                filters = meta.get('filters')
                try:
                    db.add(InteractionLibrarySearch(session_id=e.session_id, library=lib, query=q, filters=filters))
                except Exception:
                    # Don't block ingestion
                    continue
    except Exception:
        # Model/table might not exist yet in older deployments; ignore
        pass
