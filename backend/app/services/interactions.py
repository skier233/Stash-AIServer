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
from collections import defaultdict

# Normalize datetime to naive UTC for consistent comparisons/storage
def _to_naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)

# Simple in-place segment reconstruction per (session_id, scene_id)
# Based on primitive events sequence ordering by client_ts

def ingest_events(db: Session, events: Iterable[InteractionEventIn], client_fingerprint: str | None = None) -> Tuple[int,int,list[str]]:
    accepted = 0
    duplicates = 0
    errors: List[str] = []
    # Sort by client timestamp for deterministic processing
    ev_list = sorted(list(events), key=lambda e: e.ts)

    # Pre-dedupe: collect client_event_ids and query which already exist to avoid per-event selects
    client_ids = {e.id for e in ev_list if getattr(e, 'id', None) is not None}
    existing_client_ids: set = set()
    if client_ids:
        try:
            rows = db.execute(select(InteractionEvent.client_event_id).where(InteractionEvent.client_event_id.in_(list(client_ids)))).scalars().all()
            existing_client_ids = set(rows)
        except Exception:
            existing_client_ids = set()

    # Resolve incoming session ids to canonical ids once per unique incoming id
    session_resolution_cache: dict[str, str] = {}
    unique_incoming = {e.session_id for e in ev_list if getattr(e, 'session_id', None) is not None}
    for incoming in unique_incoming:
        try:
            session_resolution_cache[incoming] = _find_or_create_session_id(db, incoming, client_fingerprint)
        except Exception:
            # leave unresolved; events will error later
            pass

    # Fetch InteractionSession objects for canonical ids used in this batch to avoid per-event session queries
    canonical_ids = {sid for sid in session_resolution_cache.values() if sid is not None}
    session_obj_cache: dict[str, InteractionSession] = {}
    if canonical_ids:
        try:
            sess_rows = db.execute(select(InteractionSession).where(InteractionSession.session_id.in_(list(canonical_ids)))).scalars().all()
            session_obj_cache = {s.session_id: s for s in sess_rows}
        except Exception:
            session_obj_cache = {}

    # use module-level helper

    for ev in ev_list:
        # determine canonical session first (so stored events and summaries use same session id)
        try:
            client_ts_val = _to_naive(ev.ts)
            # find or use cached canonical session id
            sess_id = session_resolution_cache.get(ev.session_id) if ev.session_id is not None else None
            if sess_id is None and getattr(ev, 'session_id', None) is not None:
                # fallback to resolving on-the-fly
                sess_id = _find_or_create_session_id(db, ev.session_id, client_fingerprint)
                session_resolution_cache[ev.session_id] = sess_id
            # set the event's session_id to the canonical session id so we store under that session
            ev.session_id = sess_id
        except Exception as e:
            tb = traceback.format_exc()
            errors.append(f'event={getattr(ev, "id", None)} session={getattr(ev, "session_id", None)} type={getattr(ev, "type", None)} err={e} trace={tb}')
            continue

        # Dedup by client_event_id (ev.id) using pre-fetched set
        if ev.id and ev.id in existing_client_ids:
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
                sess_obj = session_obj_cache.get(ev.session_id)
                _update_session(db, obj, sess_obj)
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


def _update_session(db: Session, ev: InteractionEvent, sess: InteractionSession | None = None):
    # Accept a pre-fetched session object to avoid extra queries when available
    if sess is None:
        sess = db.execute(select(InteractionSession).where(InteractionSession.session_id==ev.session_id)).scalar_one_or_none()
    # scene_related variable removed (unused)
    # normalize event timestamps to naive UTC for comparison
    # use module-level helper

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
    # Fetch rows and delegate to the rows-based implementation
    rows = db.execute(
        select(InteractionEvent).where(
            InteractionEvent.session_id == session_id,
            InteractionEvent.entity_type == 'scene',
            InteractionEvent.entity_id == scene_id,
        ).order_by(InteractionEvent.client_ts.asc())
    ).scalars().all()
    return recompute_segments_from_rows(rows, session_id, scene_id, scene_watch_id)


def recompute_segments_from_rows(rows: list, session_id: str, scene_id: str, scene_watch_id: int, merge_gap: float = 1.0):
    """Compute segments from a list of InteractionEvent rows (ordered by client_ts).
    This mirrors recompute_segments but operates on provided rows so callers can fetch a limited window.
    """
    segments: list[tuple[float, float]] = []
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
            was_playing = last_play_start_pos is not None
            from_pos = meta.get('from')
            to_pos = meta.get('to')
            try:
                if was_playing:
                    end_pos = float(from_pos) if from_pos is not None else (last_position if last_position is not None else last_play_start_pos)
                    close_segment(end_pos)
                if to_pos is not None:
                    new_pos = float(to_pos)
                    last_position = new_pos
                    if was_playing:
                        last_play_start_pos = new_pos
                    else:
                        last_play_start_pos = None
                else:
                    last_play_start_pos = None
            except Exception:
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

    # Merge intervals using provided merge_gap
    merged: list[list[float]] = []
    for seg in sorted(segments, key=lambda s: s[0]):
        if not merged:
            merged.append([seg[0], seg[1]])
            continue
        last = merged[-1]
        if seg[0] <= last[1] + merge_gap:
            last[1] = max(last[1], seg[1])
        else:
            merged.append([seg[0], seg[1]])

    out: list[SceneWatchSegment] = []
    for s in merged:
        start, end = float(s[0]), float(s[1])
        watched = max(0.0, end - start)
        out.append(SceneWatchSegment(scene_watch_id=scene_watch_id, session_id=session_id, scene_id=scene_id, start_s=start, end_s=end, watched_s=watched))
    return out
 
    # (Removed in favor of bulk implementation)

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


def _bulk_update_scene_derived(db: Session, scene_ev_list: list, scene_ids: set[str]):
    """Efficiently update SceneDerived rows for a set of scene_ids using batched queries.

    Logic per scene:
      - view_count += number of scene_view events in this batch for that scene
      - last_viewed_at updated to latest scene_view in batch if present
      - Ensure row exists (create if absent)
      - derived_o_count = count of distinct sessions qualifying by any rule:
            * InteractionSession: last_entity == scene and (last_event_ts - session_start_ts) >= threshold
            * SceneWatch: (page_left_at - page_entered_at) >= threshold
            * SceneWatch: total_watched_s >= threshold

    All heavy queries are done in bulk across all target scenes.
    """
    if not scene_ids:
        return
    try:
        min_session_minutes = int(os.getenv('INTERACTION_MIN_SESSION_MINUTES', '10'))
    except Exception:
        min_session_minutes = 10
    min_session_seconds = min_session_minutes * 60

    # 1. Aggregate batch scene_view counts & last_view timestamps in one pass over scene_ev_list
    view_counts: dict[str,int] = defaultdict(int)
    last_view_ts: dict[str,datetime] = {}
    for e in scene_ev_list:
        # caller guarantees these are scene-relevant events and filtered to scene_ids
        if getattr(e, 'type', None) == 'scene_view' and getattr(e, 'entity_id', None) in scene_ids:
            sid = e.entity_id
            view_counts[sid] += 1
            ts = getattr(e, 'ts', None)
            if ts is not None:
                prev = last_view_ts.get(sid)
                if prev is None or ts > prev:
                    last_view_ts[sid] = ts

    # 2. Fetch existing SceneDerived rows in bulk
    existing_rows = db.execute(select(SceneDerived).where(SceneDerived.scene_id.in_(list(scene_ids)))).scalars().all()
    existing_map = {r.scene_id: r for r in existing_rows}

    # 3. Upsert basic fields (view_count, last_viewed_at)
    to_create: list[SceneDerived] = []
    for sid in scene_ids:
        row = existing_map.get(sid)
        if row:
            # increment view_count
            inc = view_counts.get(sid, 0)
            if inc:
                try:
                    row.view_count = (row.view_count or 0) + inc
                except Exception:
                    pass
            lv = last_view_ts.get(sid)
            if lv is not None:
                if row.last_viewed_at is None or lv > row.last_viewed_at:
                    row.last_viewed_at = lv
        else:
            to_create.append(SceneDerived(scene_id=sid, last_viewed_at=last_view_ts.get(sid), view_count=view_counts.get(sid,0), derived_o_count=0))
    if to_create:
        for obj in to_create:
            db.add(obj)
        try:
            db.flush()
        except Exception:
            pass
        # refresh existing_map with any newly created
        for obj in to_create:
            existing_map[obj.scene_id] = obj

    # 4. Compute derived_o_count in bulk
    qualified_sessions_by_scene: dict[str,set[str]] = {sid: set() for sid in scene_ids}

    # 4a. Sessions qualification (last entity == scene)
    try:
        session_rows = db.execute(
            select(InteractionSession).where(
                (InteractionSession.last_entity_type == 'scene') & (InteractionSession.last_entity_id.in_(list(scene_ids)))
            )
        ).scalars().all()
        for s in session_rows:
            try:
                if s.last_event_ts and s.session_start_ts:
                    dur = (s.last_event_ts - s.session_start_ts).total_seconds()
                    if dur >= min_session_seconds:
                        qualified_sessions_by_scene.setdefault(s.last_entity_id, set()).add(s.session_id)
            except Exception:
                continue
    except Exception:
        pass

    # 5. Persist derived_o_count
    for sid, row in existing_map.items():
        try:
            qual = qualified_sessions_by_scene.get(sid)
            if qual is not None:
                row.derived_o_count = len(qual)
        except Exception:
            continue
    # image-derived handled elsewhere


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
            # Normalize timestamp once to naive UTC for ordering safety
            try:
                norm_ts = _to_naive(ev.ts)
                if norm_ts is not None:
                    ev.ts = norm_ts  # safe mutation of schema object
            except Exception:
                pass
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

    # 3. Build / update SceneWatch rows
    for (sid, scene_id), sc_events in scene_events_by_pair.items():
        try:
            watch = watch_map.get((sid, scene_id))
            if watch:
                # Preserve earliest enter; only update if we don't have one or found an earlier
                for ev in sc_events:
                    if ev.type == 'scene_page_enter' or ev.type == 'scene_view':
                        if watch.page_entered_at is None or ev.ts < watch.page_entered_at:
                            watch.page_entered_at = ev.ts
                    elif ev.type == 'scene_page_leave':
                        # Only set/extend leave if it's truly later (avoid overwriting with earlier leaves)
                        if watch.page_left_at is None or ev.ts > watch.page_left_at:
                            watch.page_left_at = ev.ts
                # Session fallback ONLY if user navigated away from this scene (different last_entity)
                if watch.page_left_at is None:
                    sess = session_map.get(sid)
                    if sess:
                        try:
                            if (getattr(sess, 'last_entity_type', None) != 'scene' or getattr(sess, 'last_entity_id', None) != scene_id):
                                cand = getattr(sess, 'last_entity_event_ts', None) or getattr(sess, 'last_event_ts', None)
                                if cand and (watch.page_entered_at is None or cand >= watch.page_entered_at):
                                    watch.page_left_at = cand
                        except Exception:
                            pass
            else:
                page_entered_at = None
                page_left_at = None
                for ev in sc_events:
                    if ev.type == 'scene_page_enter' or ev.type == 'scene_view':
                        if page_entered_at is None or ev.ts < page_entered_at:
                            page_entered_at = ev.ts
                    elif ev.type == 'scene_page_leave':
                        if page_left_at is None or ev.ts > page_left_at:
                            page_left_at = ev.ts
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
        except Exception as e:  # pragma: no cover
            errors.append(f'scene_watch {sid}/{scene_id}: {e}')

    if new_watches:
        try:
            db.flush()
        except Exception as e:  # pragma: no cover
            errors.append(f'scene_watch_flush: {e}')

    # 4 & 5. Segments & derived updates (windowed replay + pointer)
    # configuration
    TIME_MARGIN_SECONDS = float(os.getenv('INTERACTION_SEGMENT_TIME_MARGIN_SECONDS', '2'))
    # Unified merge gap precedence: SEGMENT_MERGE_GAP_SECONDS > INTERACTION_SEGMENT_POS_MARGIN_SECONDS > 0.5
    MERGE_GAP_SECONDS = float(os.getenv('SEGMENT_MERGE_GAP_SECONDS', os.getenv('INTERACTION_SEGMENT_POS_MARGIN_SECONDS', '0.5')))

    for (sid, scene_id), sc_events in scene_events_by_pair.items():
        watch = watch_map.get((sid, scene_id))
        if not watch:
            continue
        try:
            if any(ev.type in watch_related_types for ev in sc_events):
                # compute batch time window
                batch_min_ts = min(ev.ts for ev in sc_events)
                batch_max_ts = max(ev.ts for ev in sc_events)

                # determine window to query (expand by margin)
                window_min = batch_min_ts - timedelta(seconds=TIME_MARGIN_SECONDS)
                window_max = batch_max_ts + timedelta(seconds=TIME_MARGIN_SECONDS)

                # fetch boundary events (one before window_min, plus additional up to 4 for context)
                before_rows_full = db.execute(
                    select(InteractionEvent).where(
                        InteractionEvent.session_id == sid,
                        InteractionEvent.entity_type == 'scene',
                        InteractionEvent.entity_id == scene_id,
                        InteractionEvent.client_ts < window_min
                    ).order_by(InteractionEvent.client_ts.desc()).limit(5)
                ).scalars().all()
                # always include at least the most recent prior event (if any) even in append-fast mode
                before_rows = before_rows_full
                after_ev = db.execute(
                    select(InteractionEvent).where(
                        InteractionEvent.session_id == sid,
                        InteractionEvent.entity_type == 'scene',
                        InteractionEvent.entity_id == scene_id,
                        InteractionEvent.client_ts > window_max
                    ).order_by(InteractionEvent.client_ts.asc()).limit(1)
                ).scalars().first()

                # fetch events inside window
                window_rows = db.execute(
                    select(InteractionEvent).where(
                        InteractionEvent.session_id == sid,
                        InteractionEvent.entity_type == 'scene',
                        InteractionEvent.entity_id == scene_id,
                        InteractionEvent.client_ts >= window_min,
                        InteractionEvent.client_ts <= window_max
                    ).order_by(InteractionEvent.client_ts.asc())
                ).scalars().all()

                # Decide if this is an append-only fast path
                # Append-fast heuristic kept but we still include at least 1 prior event for continuity
                append_fast = False
                try:
                    last_ptr = getattr(watch, 'last_processed_event_ts', None)
                    if last_ptr is not None and batch_min_ts > (last_ptr + timedelta(seconds=TIME_MARGIN_SECONDS)):
                        append_fast = True
                except Exception:
                    pass

                rows_for_replay: list[InteractionEvent] = []
                # Always keep at least one prior event (most recent) for state continuity
                if before_rows:
                    # reverse to chronological order
                    rows_for_replay.extend(reversed(before_rows))
                rows_for_replay.extend(window_rows)
                if not append_fast and after_ev:
                    rows_for_replay.append(after_ev)

                # compute new segments for this window
                new_segments = recompute_segments_from_rows(rows_for_replay, sid, scene_id, watch.id, merge_gap=MERGE_GAP_SECONDS)

                # fetch existing segments for this pair
                existing_segments = db.execute(
                    select(SceneWatchSegment).where(
                        SceneWatchSegment.session_id == sid,
                        SceneWatchSegment.scene_id == scene_id
                    ).order_by(SceneWatchSegment.start_s.asc())
                ).scalars().all()

                # Combine existing and new segments as intervals and merge them into a final set
                intervals: list[tuple[float,float]] = []
                for seg in existing_segments:
                    intervals.append((float(seg.start_s), float(seg.end_s)))
                for seg in new_segments:
                    intervals.append((float(seg.start_s), float(seg.end_s)))

                merged_intervals: list[list[float]] = []
                for seg in sorted(intervals, key=lambda s: s[0]):
                    if not merged_intervals:
                        merged_intervals.append([seg[0], seg[1]])
                        continue
                    last_iv = merged_intervals[-1]
                    if seg[0] <= last_iv[1] + MERGE_GAP_SECONDS:
                        last_iv[1] = max(last_iv[1], seg[1])
                    else:
                        merged_intervals.append([seg[0], seg[1]])

                # Convert merged intervals to SceneWatchSegment objects
                final_segments: list[SceneWatchSegment] = []  # (unused now, replaced by final_rows assembly)

                # Replace existing segments for this pair with the merged final set
                # Strategy: keep existing non-overlapping segments as-is; for overlapping regions
                # reuse one existing segment row (expand it) and delete other overlapping fragments.
                # For final intervals with no overlap, create new rows. This preserves IDs where possible.
                to_delete_ids = set()
                inserted = []

                # helper to find existing segments overlapping an interval
                def overlapping_existing(start: float, end: float) -> list:
                    out = []
                    for seg in existing_segments:
                        if not (seg.end_s < (start - MERGE_GAP_SECONDS) or seg.start_s > (end + MERGE_GAP_SECONDS)):
                            out.append(seg)
                    return out
                for interval in merged_intervals:
                    fs, fe = float(interval[0]), float(interval[1])
                    overlaps = overlapping_existing(fs, fe)
                    if overlaps:
                        # pick one existing segment as primary (prefer largest overlap)
                        overlaps_sorted = sorted(overlaps, key=lambda s: max(0.0, min(s.end_s, fe) - max(s.start_s, fs)), reverse=True)
                        primary = overlaps_sorted[0]
                        # expand primary to cover union
                        new_start = min(float(primary.start_s), fs)
                        new_end = max(float(primary.end_s), fe)
                        primary.start_s = new_start
                        primary.end_s = new_end
                        try:
                            primary.watched_s = max(0.0, new_end - new_start)
                        except Exception:
                            pass
                        # primary segment is now updated in-place
                        # mark other overlapping existing segments for deletion
                        for other in overlaps_sorted[1:]:
                            to_delete_ids.add(int(other.id))
                    else:
                        # create new segment row
                        new_seg = SceneWatchSegment(scene_watch_id=watch.id, session_id=sid, scene_id=scene_id, start_s=fs, end_s=fe, watched_s=max(0.0, fe-fs))
                        db.add(new_seg)
                        inserted.append(new_seg)

                # delete only the marked overlapping fragments (if any)
                if to_delete_ids:
                    db.execute(
                        delete(SceneWatchSegment).where(SceneWatchSegment.id.in_(list(to_delete_ids)))
                    )

                # final set for stats: combine kept existing segments (including updated primaries) and newly inserted
                final_rows: list[SceneWatchSegment] = []
                # include existing segments that were not deleted and belong to this pair
                for seg in existing_segments:
                    if int(seg.id) in to_delete_ids:
                        continue
                    # ensure the segment belongs to this pair (it will)
                    final_rows.append(seg)
                final_rows.extend(inserted)

                # Continuous playback extension: if no control events and only progress events advanced position
                control_types = {'scene_watch_start','scene_watch_pause','scene_watch_complete','scene_seek'}
                has_control = any(ev.type in control_types for ev in sc_events)
                has_progress = any(ev.type == 'scene_watch_progress' for ev in sc_events)
                if (has_progress and not has_control and not new_segments and final_rows):
                    # derive highest progress position from batch events (prefer numeric positions)
                    max_progress = None
                    for ev in sc_events:
                        if ev.type == 'scene_watch_progress':
                            try:
                                pos = (ev.metadata or {}).get('position') if hasattr(ev, 'metadata') else None
                                if pos is None:
                                    meta = getattr(ev, 'event_metadata', {}) or {}
                                    pos = meta.get('position')
                                if pos is not None:
                                    pos_f = float(pos)
                                    if max_progress is None or pos_f > max_progress:
                                        max_progress = pos_f
                            except Exception:
                                pass
                    if max_progress is not None:
                        # extend only if within acceptable gap (avoid gigantic jump without a seek)
                        last_seg = max(final_rows, key=lambda s: (float(s.end_s), float(s.start_s)))
                        if max_progress > float(last_seg.end_s) and max_progress <= float(last_seg.end_s) + (MERGE_GAP_SECONDS * 4):
                            try:
                                last_seg.end_s = max_progress
                                last_seg.watched_s = max(0.0, float(last_seg.end_s) - float(last_seg.start_s))
                            except Exception:
                                pass

                # update stats using final_rows
                _update_scene_watch_stats(db, watch, final_rows)

                # advance pointer conservatively
                try:
                    if getattr(watch, 'last_processed_event_ts', None) is None or batch_max_ts > watch.last_processed_event_ts:
                        watch.last_processed_event_ts = batch_max_ts
                        watch.processed_version = getattr(watch, 'processed_version', 0) + 1
                except Exception:
                    pass
            # defer scene_derived updates until after loop (bulk, deduped)
        except Exception as e:  # pragma: no cover
            errors.append(f'summary {sid}/{scene_id}: {e}')

    # Bulk update scene derived metrics once per unique scene to avoid double counting
    try:
        # Flatten only the scene_view events we grouped above (we only need scene_view for derived updates)
        scene_ev_list = [ev for evs in scene_events_by_pair.values() for ev in evs if getattr(ev, 'type', None) == 'scene_view']
        _bulk_update_scene_derived(db, scene_ev_list, scene_ids)
    except Exception as e:  # pragma: no cover
        errors.append(f'scene_derived_bulk: {e}')


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
