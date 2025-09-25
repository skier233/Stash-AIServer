from __future__ import annotations
from typing import Iterable, List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import datetime
from app.models.interaction import InteractionEvent, InteractionSession, SceneWatchSummary
from app.schemas.interaction import InteractionEventIn

# Simple in-place segment reconstruction per (session_id, scene_id)
# Based on primitive events sequence ordering by client_ts

def ingest_events(db: Session, events: Iterable[InteractionEventIn]) -> Tuple[int,int,list[str]]:
    accepted = 0
    duplicates = 0
    errors: List[str] = []
    # Sort by client timestamp for deterministic processing
    ev_list = sorted(list(events), key=lambda e: e.ts)

    for ev in ev_list:
        # Dedup by client_event_id (ev.id)
        if ev.id:
            existing = db.execute(select(InteractionEvent.id).where(InteractionEvent.client_event_id==ev.id)).first()
            if existing:
                duplicates += 1
                continue
        try:
            viewport_w = ev.viewport.get('w') if ev.viewport else None
            viewport_h = ev.viewport.get('h') if ev.viewport else None
            obj = InteractionEvent(
                client_event_id=ev.id,
                session_id=ev.session_id,
                event_type=ev.type,
                entity_type=ev.entity_type,
                entity_id=ev.entity_id,
                client_ts=ev.ts,
                event_metadata=ev.metadata,
                page_url=ev.page_url,
                user_agent=ev.user_agent,
                viewport_w=viewport_w,
                viewport_h=viewport_h,
                schema_version=ev.schema_version,
            )
            db.add(obj)
            accepted += 1
            _update_session(db, obj)
        except Exception as e:  # pragma: no cover (best-effort logging)
            errors.append(str(e))
    # Flush so they are queryable for aggregation
    db.flush()
    # Aggregate per session+scene touched in this batch
    touched = {(e.session_id, e.entity_id) for e in ev_list if e.entity_type=='scene'}
    for sid, scene_id in touched:
        try:
            recompute_scene_summary(db, sid, scene_id)
        except Exception as e:  # pragma: no cover
            errors.append(f'summary {sid}/{scene_id}: {e}')
    db.commit()
    return accepted, duplicates, errors


def _update_session(db: Session, ev: InteractionEvent):
    sess = db.execute(select(InteractionSession).where(InteractionSession.session_id==ev.session_id)).scalar_one_or_none()
    now = datetime.utcnow()
    scene_related = ev.entity_type=='scene'
    if not sess:
        sess = InteractionSession(session_id=ev.session_id, last_event_ts=now)
        if scene_related:
            sess.last_scene_id = ev.entity_id
            sess.last_scene_event_ts = now
        db.add(sess)
    else:
        if ev.client_ts > sess.last_event_ts:
            sess.last_event_ts = ev.client_ts
        if scene_related:
            sess.last_scene_id = ev.entity_id
            sess.last_scene_event_ts = ev.client_ts


def recompute_scene_summary(db: Session, session_id: str, scene_id: str):
    # Pull relevant events ordered by client_ts
    rows = db.execute(select(InteractionEvent).where(
        InteractionEvent.session_id==session_id,
        InteractionEvent.entity_type=='scene',
        InteractionEvent.entity_id==scene_id
    ).order_by(InteractionEvent.client_ts.asc())).scalars().all()

    segments: list[tuple[float,float]] = []
    last_play_start_pos: float | None = None
    last_play_start_ts: datetime | None = None
    last_position: float | None = None

    def close_segment(end_pos: float):
        nonlocal last_play_start_pos, last_play_start_ts, segments, last_position
        if last_play_start_pos is None:
            return
        start_pos = last_play_start_pos
        if end_pos > start_pos:
            segments.append((start_pos, end_pos))
        last_play_start_pos = None
        last_play_start_ts = None
        last_position = end_pos

    for ev in rows:
        meta = ev.event_metadata or {}
        et = ev.event_type
        if et == 'scene_watch_start':
            last_play_start_pos = (meta.get('position')
                if meta.get('position') is not None else (last_position or 0.0))
            last_play_start_ts = ev.client_ts
        elif et == 'scene_seek':
            # Just update position reference; if seeking during play we might close segment first
            to_pos = meta.get('to')
            if to_pos is not None:
                if last_play_start_pos is not None and last_position is not None:
                    # close current segment up to previous position before jump
                    close_segment(last_position)
                last_position = float(to_pos)
                # If still playing, treat seek as new play start at new position
                if last_play_start_pos is not None:
                    last_play_start_pos = last_position
        elif et in ('scene_watch_pause','scene_watch_complete'):
            # Prefer precise position if present
            pos = meta.get('position')
            if pos is None and last_position is not None:
                pos = last_position
            if pos is None and last_play_start_pos is not None and last_play_start_ts is not None:
                pos = last_play_start_pos  # fallback
            if pos is not None:
                close_segment(float(pos))
        elif et == 'scene_watch_progress':
            pos = meta.get('position')
            if pos is not None:
                last_position = float(pos)
        # Ignore other event types

    # Merge overlapping/adjacent segments (<=1s gap)
    merged: list[tuple[float,float]] = []
    for seg in sorted(segments, key=lambda s: s[0]):
        if not merged:
            merged.append(list(seg))  # type: ignore
            continue
        last = merged[-1]
        if seg[0] <= last[1] + 1.0:
            last[1] = max(last[1], seg[1])
        else:
            merged.append(list(seg))  # type: ignore

    total = sum(s[1]-s[0] for s in merged)
    completed = any(ev.event_type=='scene_watch_complete' for ev in rows)
    # Duration may be present in complete event metadata
    duration = None
    for ev in rows:
        if ev.event_type=='scene_watch_complete':
            md = ev.event_metadata or {}
            dur = md.get('duration') or md.get('duration_s')
            if dur is not None:
                try:
                    duration = float(dur)
                except Exception:
                    pass
            break
    percent = (total/duration*100.0) if duration and duration>0 else None

    existing = db.execute(select(SceneWatchSummary).where(SceneWatchSummary.session_id==session_id, SceneWatchSummary.scene_id==scene_id)).scalar_one_or_none()
    seg_payload = [{'start': float(s[0]), 'end': float(s[1])} for s in merged]
    if existing:
        existing.total_watched_s = float(total)
        existing.duration_s = duration
        existing.percent_watched = percent
        existing.completed = 1 if completed else 0
        existing.segments = seg_payload
    else:
        obj = SceneWatchSummary(
            session_id=session_id,
            scene_id=scene_id,
            total_watched_s=float(total),
            duration_s=duration,
            percent_watched=percent,
            completed=1 if completed else 0,
            segments=seg_payload,
        )
        db.add(obj)
