"""API endpoints for the extensible entity rating system.

Provides GET / PUT / DELETE for arbitrary entity ratings.
Values are stored as integers 0-100 (matching Stash's ``rating100``).
"""
from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert

from stash_ai_server.db.session import get_session_local
from stash_ai_server.models.ratings import EntityRating

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/ratings", tags=["ratings"])

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class RatingValue(BaseModel):
    value: int = Field(..., ge=0, le=100, description="Rating value 0-100")


class RatingOut(BaseModel):
    entity_type: str
    entity_id: str
    rating_key: str
    value: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/{entity_type}/{entity_id}")
async def get_ratings(entity_type: str, entity_id: str) -> dict[str, Any]:
    """Return all ratings for an entity."""
    with get_session_local()() as session:
        rows = session.execute(
            sa.select(EntityRating).where(
                EntityRating.entity_type == entity_type,
                EntityRating.entity_id == entity_id,
            )
        ).scalars().all()
        return {
            "ratings": [
                {
                    "rating_key": r.rating_key,
                    "value": r.value,
                }
                for r in rows
            ]
        }


@router.put("/{entity_type}/{entity_id}/{rating_key}")
async def set_rating(
    entity_type: str,
    entity_id: str,
    rating_key: str,
    body: RatingValue,
) -> RatingOut:
    """Set (upsert) a rating for an entity."""
    with get_session_local()() as session:
        stmt = (
            pg_insert(EntityRating)
            .values(
                entity_type=entity_type,
                entity_id=entity_id,
                rating_key=rating_key,
                value=body.value,
            )
            .on_conflict_do_update(
                constraint="uq_entity_rating",
                set_={
                    "value": body.value,
                    "updated_at": sa.text("now()"),
                },
            )
            .returning(EntityRating)
        )
        result = session.execute(stmt).scalars().first()
        session.commit()
        return RatingOut(
            entity_type=result.entity_type,
            entity_id=result.entity_id,
            rating_key=result.rating_key,
            value=result.value,
        )


@router.delete("/{entity_type}/{entity_id}/{rating_key}")
async def delete_rating(
    entity_type: str,
    entity_id: str,
    rating_key: str,
) -> dict[str, bool]:
    """Remove a rating."""
    with get_session_local()() as session:
        deleted = session.execute(
            sa.delete(EntityRating).where(
                EntityRating.entity_type == entity_type,
                EntityRating.entity_id == entity_id,
                EntityRating.rating_key == rating_key,
            )
        ).rowcount
        session.commit()
        if deleted == 0:
            raise HTTPException(status_code=404, detail="Rating not found")
        return {"deleted": True}
