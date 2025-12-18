from __future__ import annotations
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session
from stash_ai_server.models.recommendation import RecommendationPreference


def _context_value(context: Any) -> str:
    if context is None:
        return ''
    if hasattr(context, 'value'):
        return getattr(context, 'value')
    return str(context)


def get_preference(db: Session, context: Any) -> RecommendationPreference | None:
    ctx = _context_value(context)
    if not ctx:
        return None
    stmt = select(RecommendationPreference).where(RecommendationPreference.context == ctx)
    return db.execute(stmt).scalar_one_or_none()


def save_preference(
    db: Session,
    context: Any,
    recommender_id: str,
    config: dict | None,
) -> RecommendationPreference:
    ctx = _context_value(context)
    row = get_preference(db, ctx)
    if row is None:
        row = RecommendationPreference(context=ctx, recommender_id=recommender_id, config=config or {})
        db.add(row)
    else:
        row.recommender_id = recommender_id
        row.config = config or {}
    db.commit()
    db.refresh(row)
    return row