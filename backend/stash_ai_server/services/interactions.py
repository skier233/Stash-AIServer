from __future__ import annotations
import logging
from typing import Iterable, List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import select, delete, update
from datetime import datetime, timezone, timedelta
import traceback
import os
from stash_ai_server.core.system_settings import get_value as sys_get
from collections import defaultdict
from stash_ai_server.utils.string_utils import normalize_null_strings

from stash_ai_server.models.interaction import (
    InteractionEvent,
    InteractionSession,
    SceneWatch,
    SceneWatchSegment,
    SceneDerived,
    ImageDerived,
    InteractionSessionAlias,
)
from sqlalchemy.exc import IntegrityError
from stash_ai_server.schemas.interaction import InteractionEventIn

CONTROL_EVENT_TYPES = {'scene_watch_start', 'scene_watch_pause', 'scene_watch_complete', 'scene_seek'}

_log = logging.getLogger(__name__)

class _SyntheticInteractionEvent:
    __slots__ = ('id', 'client_event_id', 'session_id', 'event_type', 'entity_type', 'entity_id', 'client_ts', 'event_metadata')

    def __init__(self, *, client_event_id: str | None, session_id: str, event_type: str, entity_type: str, entity_id: int, client_ts: datetime, event_metadata: dict | None):
        self.id = None
        self.client_event_id = client_event_id
        self.session_id = session_id
        self.event_type = event_type
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.client_ts = client_ts
        self.event_metadata = event_metadata

# Optional import: older deployments might not have this model; keep None to preserve best-effort behavior
try:
    from stash_ai_server.models.interaction import InteractionLibrarySearch
except Exception:
    InteractionLibrarySearch = None

def _to_naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)

# Reconstruct segments for a session+scene from ordered events

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
            setattr(ev, '_client_ts_naive', client_ts_val)
            # find or use cached canonical session id
            sess_id = session_resolution_cache.get(ev.session_id) if ev.session_id is not None else None
            # Normalize event metadata to convert string nulls to None
            if hasattr(ev, 'metadata'):
                ev.metadata = normalize_null_strings(ev.metadata)
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

        client_ts_val = getattr(ev, '_client_ts_naive', None)
        store_event = ev.type != 'scene_watch_progress'

        try:
            # Use a nested transaction (savepoint) so a failing event doesn't roll back others
            with db.begin_nested():
                sess_obj = session_obj_cache.get(ev.session_id)
                if store_event:
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
                    _update_session(db, obj, sess_obj)
                else:
                    # For progress events, update session state without persisting the event row
                    virtual = _SyntheticInteractionEvent(
                        client_event_id=ev.id,
                        session_id=ev.session_id,
                        event_type=ev.type,
                        entity_type=ev.entity_type,
                        entity_id=ev.entity_id,
                        client_ts=client_ts_val or datetime.utcnow(),
                        event_metadata=ev.metadata,
                    )
                    _update_session(db, virtual, sess_obj)
            accepted += 1
            if store_event and ev.id:
                existing_client_ids.add(ev.id)
        except Exception as e:  # pragma: no cover (best-effort logging)
            tb = traceback.format_exc()
            errors.append(f'event={getattr(ev, "id", None)} session={getattr(ev, "session_id", None)} type={getattr(ev, "type", None)} err={e} trace={tb}')
    # Flush so events are queryable for aggregation helpers
    db.flush()
    # Aggregate & derived updates
    _process_scene_summaries(db, ev_list, errors)
    _process_image_derived(db, ev_list, errors)
    _persist_library_search_events(db, ev_list)
    db.commit()
    return accepted, duplicates, errors


def _update_session(db: Session, ev: InteractionEvent, sess: InteractionSession | None = None):
    # Accept a pre-fetched session object to avoid extra queries
    if sess is None:
        sess = db.execute(select(InteractionSession).where(InteractionSession.session_id==ev.session_id)).scalar_one_or_none()
    # normalize event timestamps to naive UTC
    ev_client_ts = _to_naive(ev.client_ts)

    if not sess:
        raise ValueError(f'session not found for event {ev.id} session_id={ev.session_id}')

    if ev_client_ts and (sess.last_event_ts is None or ev_client_ts > sess.last_event_ts):
        sess.last_event_ts = ev_client_ts
    # Update generic last-entity for relevant event types
    try:
        if ev.entity_type in ('scene', 'image', 'gallery'):
            sess.last_entity_type = ev.entity_type
            sess.last_entity_id = ev.entity_id
            sess.last_entity_event_ts = ev_client_ts
    except Exception:
        pass
    # session-level events may include a final last_entity; prefer event-provided timestamps when parseable
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
                        sess.last_entity_id = i
                        if ts:
                            try:
                                parsed = datetime.fromisoformat(ts)
                                sess.last_entity_event_ts = parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
                            except Exception:
                                # fallback: epoch ms or use last event
                                if str(ts).isdigit():
                                    sess.last_entity_event_ts = datetime.utcfromtimestamp(int(ts) / 1000.0)
                                else:
                                    sess.last_entity_event_ts = ev_client_ts or datetime.now(timezone.utc)
                    except Exception:
                        pass
    except Exception:
        pass

def _finalize_stale_sessions_for_fingerprint(db: Session, client_fingerprint: str, time_threshold: datetime):
    """Finalize stale sessions and increment derived counts for their last viewed entity."""
    try:
        # Select candidate sessions to finalize
        stale_sessions: list[InteractionSession] = db.execute(
            select(InteractionSession).where(
                InteractionSession.client_fingerprint == client_fingerprint,
                InteractionSession.ended_at.is_(None),
                InteractionSession.last_event_ts < time_threshold
            )
        ).scalars().all()
    except Exception:
        return
    if not stale_sessions:
        return

    # Qualification threshold (reuse existing config)
    try:
        min_session_minutes = int(sys_get('INTERACTION_MIN_SESSION_MINUTES', 10))
    except Exception:
        min_session_minutes = 10
    min_session_seconds = min_session_minutes * 60

    scene_counts: dict[int, int] = defaultdict(int)
    image_counts: dict[int, int] = defaultdict(int)
    finalize_ids: list[int] = []

    for s in stale_sessions:
        try:
            if not (s.last_event_ts and s.session_start_ts):
                continue
            dur = (s.last_event_ts - s.session_start_ts).total_seconds()
            if dur < min_session_seconds:
                # session too short; still finalize but no derived credit
                finalize_ids.append(s.id)
                continue
            ent_type = getattr(s, 'last_entity_type', None)
            ent_id = getattr(s, 'last_entity_id', None)
            if not ent_type or not ent_id:
                finalize_ids.append(s.id)
                continue
            if ent_type == 'scene':
                scene_counts[ent_id] += 1
            elif ent_type == 'image':
                image_counts[ent_id] += 1
            finalize_ids.append(s.id)
        except Exception:
            continue

    if finalize_ids:
        try:
            db.execute(
                update(InteractionSession)
                .where(InteractionSession.id.in_(finalize_ids))
                .values(ended_at=InteractionSession.last_event_ts)
            )
        except Exception:
            pass

    # Increment scene derived counts
    if scene_counts:
        existing = db.execute(select(SceneDerived).where(SceneDerived.scene_id.in_(list(scene_counts.keys())))).scalars().all()
        existing_map = {r.scene_id: r for r in existing}
        for sid, inc in scene_counts.items():
            row = existing_map.get(sid)
            if row:
                try:
                    row.derived_o_count = (row.derived_o_count or 0) + inc
                except Exception:
                    pass
            else:
                db.add(SceneDerived(scene_id=sid, derived_o_count=inc, view_count=inc, last_viewed_at=None))

    # Increment image derived counts
    if image_counts:
        existing = db.execute(select(ImageDerived).where(ImageDerived.image_id.in_(list(image_counts.keys())))).scalars().all()
        existing_map = {r.image_id: r for r in existing}
        for iid, inc in image_counts.items():
            row = existing_map.get(iid)
            if row:
                try:
                    row.derived_o_count = (row.derived_o_count or 0) + inc
                except Exception:
                    pass
            else:
                db.add(ImageDerived(image_id=iid, derived_o_count=inc, view_count=inc, last_viewed_at=None))

    # Flush so increments are persisted before new session proceeds
    try:
        db.flush()
    except Exception:
        pass


def _find_or_create_session_id(db: Session, incoming_session_id: str, client_fingerprint: str | None):
    """Resolve or create a canonical session id for an incoming id.

    Behaviors: direct match, alias lookup, fingerprint merge, or new session creation.
    """

    # 1) Direct session id exists -> canonical
    direct = db.execute(select(InteractionSession.session_id).where(InteractionSession.session_id == incoming_session_id)).first()
    if direct:
        return incoming_session_id

    now = datetime.now(timezone.utc)
    merge_ttl_seconds = int(sys_get('INTERACTION_MERGE_TTL_SECONDS', 120))
    time_threshold = now - timedelta(seconds=merge_ttl_seconds)

    # 2) Alias mapping exists -> return canonical pointed id
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

    # 3) Fingerprint merge: recent non-finalized session for same fingerprint
    if client_fingerprint:
        try:
            recent = db.execute(
                select(InteractionSession.session_id).where(
                    InteractionSession.client_fingerprint == client_fingerprint,
                    InteractionSession.last_event_ts >= time_threshold,
                    InteractionSession.ended_at.is_(None)
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
        # Finalize stale sessions for this fingerprint before creating a new one
        if client_fingerprint:
            _finalize_stale_sessions_for_fingerprint(db, client_fingerprint, time_threshold)
        db.add(InteractionSession(session_id=incoming_session_id, last_event_ts=now, session_start_ts=now, client_fingerprint=client_fingerprint))
        db.flush()
        return incoming_session_id
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        # Race: another process created it meanwhile
        direct = db.execute(select(InteractionSession.session_id).where(InteractionSession.session_id == incoming_session_id)).first()
        if direct:
            return direct[0]
        raise


def recompute_segments(db: Session, session_id: str, scene_id: int, scene_watch_id: int):
    # Fetch rows and delegate to the rows-based implementation
    try:
        min_duration = float(sys_get('SEGMENT_MIN_DURATION_SECONDS', 1.5) or 1.5)
    except Exception:
        min_duration = 1.5
    rows = db.execute(
        select(InteractionEvent).where(
            InteractionEvent.session_id == session_id,
            InteractionEvent.entity_type == 'scene',
            InteractionEvent.entity_id == scene_id,
        ).order_by(InteractionEvent.client_ts.asc())
    ).scalars().all()
    return recompute_segments_from_rows(rows, session_id, scene_id, scene_watch_id, merge_gap=1.0, min_duration=min_duration)


def recompute_segments_from_rows(rows: list, session_id: str, scene_id: int, scene_watch_id: int, merge_gap: float = 1.0, min_duration: float = 0.0):
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
            start_pos_raw = meta.get('position')
            start_pos = float(start_pos_raw) if start_pos_raw is not None else (last_position or 0.0)
            last_play_start_pos = start_pos
            last_position = start_pos
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
                if last_play_start_pos is None:
                    last_play_start_pos = last_position

    # Merge adjacent/nearby intervals using merge_gap
    merged: list[list[float]] = []
    if last_play_start_pos is not None:
        end_candidate = last_position if last_position is not None else last_play_start_pos
        try:
            end_value = float(end_candidate)
        except Exception:
            end_value = None
        if end_value is not None and end_value > float(last_play_start_pos):
            segments.append((float(last_play_start_pos), end_value))
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
        if watched >= max(0.0, float(min_duration)):
            out.append(SceneWatchSegment(scene_watch_id=scene_watch_id, session_id=session_id, scene_id=scene_id, start_s=start, end_s=end, watched_s=watched))
    return out


def _update_scene_watch_stats(db: Session, scene_watch: SceneWatch, segments: list[SceneWatchSegment]):
    """Update scene watch statistics based on computed segments"""
    total_watched = sum(seg.watched_s for seg in segments)
    scene_watch.total_watched_s = total_watched
    
    # Try to compute watch percentage if we can determine video duration
    # We now include duration on multiple watch event types (start/pause/progress/complete)
    duration = None
    WATCH_DURATION_TYPES = {'scene_watch_complete', 'scene_watch_pause', 'scene_watch_progress', 'scene_watch_start'}
    try:
        duration_event = db.execute(
            select(InteractionEvent).where(
                InteractionEvent.session_id == scene_watch.session_id,
                InteractionEvent.entity_type == 'scene',
                InteractionEvent.entity_id == scene_watch.scene_id,
                InteractionEvent.event_type.in_(WATCH_DURATION_TYPES)
            ).order_by(InteractionEvent.client_ts.desc()).limit(1)
        ).scalars().first()
        if duration_event:
            meta = duration_event.event_metadata or {}
            d = meta.get('duration')
            try:
                if d is not None and float(d) > 0:
                    duration = float(d)
            except Exception:
                pass
    except Exception:
        duration = None

    # If explicit duration not found, infer from page_entered/page_left timestamps
    if duration is None:
        try:
            if scene_watch.page_entered_at and scene_watch.page_left_at:
                dur = (scene_watch.page_left_at - scene_watch.page_entered_at).total_seconds()
                if dur > 0:
                    duration = dur
        except Exception:
            duration = None

    # Only set watch_percent when duration appears reliable
    if duration and duration > 0:
        try:
            scene_watch.watch_percent = min(100.0, (total_watched / float(duration)) * 100.0)
        except Exception:
            pass


def update_image_derived(db: Session, image_id: int, ev_list: list):
    # find view events for this image in the batch
    last_view = None
    view_events = 0
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



def _bulk_update_scene_derived(db: Session, scene_ev_list: list, scene_ids: set[int]):
    """Efficiently update SceneDerived rows for a set of scene_ids using batched queries.

    Logic per scene:
      - view_count += number of scene_view events in this batch for that scene
      - last_viewed_at updated to latest scene_view in batch if present
      - Ensure row exists (create if absent)

    """
    if not scene_ids:
        return
    try:
        min_session_minutes = int(sys_get('INTERACTION_MIN_SESSION_MINUTES', 10))
    except Exception:
        min_session_minutes = 10
    min_session_seconds = min_session_minutes * 60

    # 1. Aggregate scene_view counts & last_view timestamps in one pass
    view_counts: dict[int,int] = defaultdict(int)
    last_view_ts: dict[int,datetime] = {}
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

    # 3. Upsert basic fields (view_count, last_viewed_at). derived_o_count is handled elsewhere.
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

    # derived_o_count intentionally NOT recomputed here; it's incremented at session finalization time.


# -------------------------- Helper aggregation sections --------------------------
def _process_scene_summaries(db: Session, ev_list: list, errors: list[str]):
    """Aggregate per-(session,scene) updates.

    Group events by (session,scene), load relevant watches and sessions in bulk,
    then compute segments and stats only for pairs that need it.
    """
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

    # 2. Bulk fetch existing watches and sessions
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

    # event types that require segment recomputation
    watch_related_types = {'scene_watch_start', 'scene_watch_pause', 'scene_watch_complete', 'scene_watch_progress', 'scene_seek'}

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
                # Session fallback only if user navigated away from this scene (different last_entity)
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
                # Infer leave only if user clearly navigated away
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
                # If still None, keep None (page considered active)
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
    TIME_MARGIN_SECONDS = float(sys_get('INTERACTION_SEGMENT_TIME_MARGIN_SECONDS', 2) or 2)
    MERGE_GAP_SECONDS = float(sys_get('SEGMENT_MERGE_GAP_SECONDS', sys_get('INTERACTION_SEGMENT_POS_MARGIN_SECONDS', 0.5)))
    try:
        MIN_SEGMENT_SECONDS = float(sys_get('SEGMENT_MIN_DURATION_SECONDS', 1.5) or 1.5)
    except Exception:
        MIN_SEGMENT_SECONDS = 1.5

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
                if not any(getattr(ev, 'event_type', None) in CONTROL_EVENT_TYPES for ev in before_rows_full):
                    control_row = db.execute(
                        select(InteractionEvent).where(
                            InteractionEvent.session_id == sid,
                            InteractionEvent.entity_type == 'scene',
                            InteractionEvent.entity_id == scene_id,
                            InteractionEvent.event_type.in_(CONTROL_EVENT_TYPES),
                            InteractionEvent.client_ts < window_min
                        ).order_by(InteractionEvent.client_ts.desc()).limit(1)
                    ).scalars().first()
                    if control_row is not None:
                        if all(control_row.id != getattr(existing, 'id', None) for existing in before_rows_full):
                            before_rows_full.append(control_row)
                        before_rows_full.sort(key=lambda ev: getattr(ev, 'client_ts', datetime.min), reverse=True)
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

                # Decide if this is an append-only fast path; we still include at least 1 prior event
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

                synthetic_progress_rows: list[_SyntheticInteractionEvent] = []
                for ev in sc_events:
                    if getattr(ev, 'type', None) != 'scene_watch_progress':
                        continue
                    ts = getattr(ev, '_client_ts_naive', None)
                    if ts is None:
                        ts = _to_naive(getattr(ev, 'ts', None))
                    if ts is None:
                        continue
                    synthetic_progress_rows.append(
                        _SyntheticInteractionEvent(
                            client_event_id=getattr(ev, 'id', None),
                            session_id=sid,
                            event_type='scene_watch_progress',
                            entity_type='scene',
                            entity_id=scene_id,
                            client_ts=ts,
                            event_metadata=getattr(ev, 'metadata', None),
                        )
                    )
                if synthetic_progress_rows:
                    rows_for_replay.extend(synthetic_progress_rows)
                    rows_for_replay.sort(key=lambda row: getattr(row, 'client_ts', datetime.min))

                # compute new segments for this window
                new_segments = recompute_segments_from_rows(
                    rows_for_replay,
                    sid,
                    scene_id,
                    watch.id,
                    merge_gap=MERGE_GAP_SECONDS,
                    min_duration=MIN_SEGMENT_SECONDS,
                )

                # fetch existing segments for this pair
                existing_segments = db.execute(
                    select(SceneWatchSegment).where(
                        SceneWatchSegment.session_id == sid,
                        SceneWatchSegment.scene_id == scene_id
                    ).order_by(SceneWatchSegment.start_s.asc())
                ).scalars().all()

                # Combine existing and new segments as intervals and merge them
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

                filtered_intervals: list[list[float]] = []
                for seg in merged_intervals:
                    if float(seg[1]) - float(seg[0]) >= MIN_SEGMENT_SECONDS:
                        filtered_intervals.append([seg[0], seg[1]])

                # Convert merged intervals to SceneWatchSegment objects
                final_segments: list[SceneWatchSegment] = []  # (unused now, replaced by final_rows assembly)

                # Replace existing segments for this pair with the merged final set.
                # Strategy: prefer reusing an existing row for overlapping intervals, delete smaller overlaps,
                # and create new rows for intervals with no overlap. This preserves row ids where practical.
                to_delete_ids = set()
                inserted = []

                # helper to find existing segments overlapping an interval
                def overlapping_existing(start: float, end: float) -> list:
                    out = []
                    for seg in existing_segments:
                        if not (seg.end_s < (start - MERGE_GAP_SECONDS) or seg.start_s > (end + MERGE_GAP_SECONDS)):
                            out.append(seg)
                    return out
                for interval in filtered_intervals:
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
                        new_seg = SceneWatchSegment(scene_watch_id=watch.id, session_id=sid, scene_id=scene_id, start_s=fs, end_s=fe, watched_s=max(0.0, fe - fs))
                        db.add(new_seg)
                        inserted.append(new_seg)

                short_existing_ids = [
                    int(seg.id)
                    for seg in existing_segments
                    if float(seg.end_s) - float(seg.start_s) < MIN_SEGMENT_SECONDS
                ]
                to_delete_ids.update(short_existing_ids)

                # final set for stats: combine kept existing segments (including updated primaries) and newly inserted
                final_rows: list[SceneWatchSegment] = []
                # include existing segments that were not deleted and belong to this pair
                for seg in existing_segments:
                    if int(seg.id) in to_delete_ids:
                        continue
                    # ensure the segment belongs to this pair (it will)
                    final_rows.append(seg)
                final_rows.extend(inserted)

                if to_delete_ids:
                    db.execute(
                        delete(SceneWatchSegment).where(SceneWatchSegment.id.in_(list(to_delete_ids)))
                    )

                # Continuous-playback heuristic: if only progress events advanced position, extend last segment
                has_control = any(ev.type in CONTROL_EVENT_TYPES for ev in sc_events)
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
                q = normalize_null_strings(meta.get('query'))
                filters = normalize_null_strings(meta.get('filters'))
                try:
                    db.add(InteractionLibrarySearch(session_id=e.session_id, library=lib, query=q, filters=filters))
                except Exception:
                    # Don't block ingestion
                    continue
    except Exception:
        # Model/table might not exist yet in older deployments; ignore
        pass
