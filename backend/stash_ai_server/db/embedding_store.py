"""Store and query functions for entity-level content embeddings.

Follows the same patterns as ``detection_store.py`` — synchronous core
with ``_async`` wrappers via ``asyncio.to_thread``.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np
import sqlalchemy as sa
from sqlalchemy import select, delete, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from stash_ai_server.db.session import get_session_local
from stash_ai_server.models.entity_embeddings import EntityEmbedding

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes for return types
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class StoredEmbedding:
    """Lightweight read-only view of a stored embedding."""
    id: int
    entity_type: str
    entity_id: int
    embedding_type: str
    embedding: list[float]
    dim: int
    embedder: str
    norm: float
    sample_count: int
    start_time: float | None
    end_time: float | None
    metadata: dict | None


@dataclass(slots=True)
class SimilarEntity:
    """Result from a similarity search."""
    entity_type: str
    entity_id: int
    distance: float
    embedding_type: str


# ---------------------------------------------------------------------------
# Store functions
# ---------------------------------------------------------------------------

def upsert_scene_embedding(
    session: Session,
    *,
    entity_type: str,
    entity_id: int,
    embedding_type: str,
    embedding: list[float],
    dim: int,
    embedder: str,
    norm: float,
    sample_count: int = 1,
    run_id: int | None = None,
    start_time: float | None = None,
    end_time: float | None = None,
    metadata: dict | None = None,
) -> int:
    """Insert or update a scene embedding (upsert on unique constraint).

    Returns the row id.
    """
    now = dt.datetime.now(dt.timezone.utc)
    # Ensure embedding is a plain list of floats (pgvector expects a list, not a string)
    vec = [float(v) for v in embedding]

    stmt = pg_insert(EntityEmbedding).values(
        run_id=run_id,
        entity_type=entity_type,
        entity_id=entity_id,
        embedding_type=embedding_type,
        embedding=vec,
        dim=dim,
        embedder=embedder,
        norm=norm,
        sample_count=sample_count,
        start_time=start_time,
        end_time=end_time,
        metadata_=metadata,
        created_at=now,
        updated_at=now,
    ).on_conflict_do_update(
        constraint="uq_entity_embedding_entity_type_embedder",
        set_={
            "embedding": vec,
            "dim": dim,
            "norm": norm,
            "sample_count": sample_count,
            "run_id": run_id,
            "start_time": start_time,
            "end_time": end_time,
            "metadata": metadata,
            "updated_at": now,
        },
    )
    result = session.execute(stmt)
    session.flush()
    # pg_insert returns the row via RETURNING if configured; fall back to query
    row_id = result.inserted_primary_key[0] if result.inserted_primary_key else None
    if row_id is None:
        row = session.execute(
            select(EntityEmbedding.id).where(
                EntityEmbedding.entity_type == entity_type,
                EntityEmbedding.entity_id == entity_id,
                EntityEmbedding.embedding_type == embedding_type,
                EntityEmbedding.embedder == embedder,
            )
        ).scalar_one()
        row_id = row
    return row_id


def store_scene_embeddings_batch(
    *,
    entity_type: str,
    entity_id: int,
    embeddings: Sequence[Mapping[str, Any]],
    run_id: int | None = None,
) -> list[int]:
    """Store multiple embeddings for one entity in a single transaction.

    Each item in ``embeddings`` should have keys:
        embedding_type, embedding (list[float]), dim, embedder, norm,
        sample_count (optional), metadata (optional).

    Returns list of row ids.
    """
    ids = []
    with get_session_local()() as session:
        for emb in embeddings:
            row_id = upsert_scene_embedding(
                session,
                entity_type=entity_type,
                entity_id=entity_id,
                embedding_type=emb["embedding_type"],
                embedding=emb["embedding"],
                dim=emb["dim"],
                embedder=emb["embedder"],
                norm=emb["norm"],
                sample_count=emb.get("sample_count", 1),
                run_id=run_id,
                start_time=emb.get("start_time"),
                end_time=emb.get("end_time"),
                metadata=emb.get("metadata"),
            )
            ids.append(row_id)
        session.commit()
    return ids


async def store_scene_embeddings_batch_async(
    *,
    entity_type: str,
    entity_id: int,
    embeddings: Sequence[Mapping[str, Any]],
    run_id: int | None = None,
) -> list[int]:
    return await asyncio.to_thread(
        store_scene_embeddings_batch,
        entity_type=entity_type,
        entity_id=entity_id,
        embeddings=embeddings,
        run_id=run_id,
    )


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------

def get_entity_embeddings(
    *,
    entity_type: str,
    entity_id: int,
    embedding_type: str | None = None,
) -> list[StoredEmbedding]:
    """Fetch all embeddings for an entity, optionally filtered by type."""
    with get_session_local()() as session:
        q = select(EntityEmbedding).where(
            EntityEmbedding.entity_type == entity_type,
            EntityEmbedding.entity_id == entity_id,
        )
        if embedding_type is not None:
            q = q.where(EntityEmbedding.embedding_type == embedding_type)
        rows = session.execute(q).scalars().all()
        return [
            StoredEmbedding(
                id=r.id,
                entity_type=r.entity_type,
                entity_id=r.entity_id,
                embedding_type=r.embedding_type,
                embedding=list(r.embedding) if r.embedding is not None else [],
                dim=r.dim,
                embedder=r.embedder,
                norm=r.norm,
                sample_count=r.sample_count,
                start_time=r.start_time,
                end_time=r.end_time,
                metadata=r.metadata_,
            )
            for r in rows
        ]


async def get_entity_embeddings_async(
    *,
    entity_type: str,
    entity_id: int,
    embedding_type: str | None = None,
) -> list[StoredEmbedding]:
    return await asyncio.to_thread(
        get_entity_embeddings,
        entity_type=entity_type,
        entity_id=entity_id,
        embedding_type=embedding_type,
    )


def find_similar_entities(
    *,
    embedding_type: str,
    query_vector: list[float],
    limit: int = 50,
    exclude_entity_id: int | None = None,
    entity_type: str = "scene",
) -> list[SimilarEntity]:
    """Find entities with the most similar embeddings using cosine distance.

    Returns results sorted by ascending distance (most similar first).
    """
    vec_str = "[" + ",".join(str(float(v)) for v in query_vector) + "]"

    with get_session_local()() as session:
        # pgvector cosine distance operator: <=>
        distance_expr = EntityEmbedding.embedding.cosine_distance(vec_str)

        q = (
            select(
                EntityEmbedding.entity_type,
                EntityEmbedding.entity_id,
                distance_expr.label("distance"),
                EntityEmbedding.embedding_type,
            )
            .where(
                EntityEmbedding.embedding_type == embedding_type,
                EntityEmbedding.entity_type == entity_type,
            )
            .order_by(distance_expr)
            .limit(limit)
        )

        if exclude_entity_id is not None:
            q = q.where(EntityEmbedding.entity_id != exclude_entity_id)

        rows = session.execute(q).all()
        return [
            SimilarEntity(
                entity_type=r.entity_type,
                entity_id=r.entity_id,
                distance=float(r.distance),
                embedding_type=r.embedding_type,
            )
            for r in rows
        ]


async def find_similar_entities_async(
    *,
    embedding_type: str,
    query_vector: list[float],
    limit: int = 50,
    exclude_entity_id: int | None = None,
    entity_type: str = "scene",
) -> list[SimilarEntity]:
    return await asyncio.to_thread(
        find_similar_entities,
        embedding_type=embedding_type,
        query_vector=query_vector,
        limit=limit,
        exclude_entity_id=exclude_entity_id,
        entity_type=entity_type,
    )


def delete_entity_embeddings(
    *,
    entity_type: str,
    entity_id: int,
    embedding_type: str | None = None,
) -> int:
    """Delete embeddings for an entity, optionally filtered by type.

    Returns the number of rows deleted.
    """
    with get_session_local()() as session:
        q = delete(EntityEmbedding).where(
            EntityEmbedding.entity_type == entity_type,
            EntityEmbedding.entity_id == entity_id,
        )
        if embedding_type is not None:
            q = q.where(EntityEmbedding.embedding_type == embedding_type)
        result = session.execute(q)
        session.commit()
        return result.rowcount


async def delete_entity_embeddings_async(
    *,
    entity_type: str,
    entity_id: int,
    embedding_type: str | None = None,
) -> int:
    return await asyncio.to_thread(
        delete_entity_embeddings,
        entity_type=entity_type,
        entity_id=entity_id,
        embedding_type=embedding_type,
    )


def delete_entity_embeddings_by_prefix(
    *,
    entity_type: str,
    entity_id: int,
    embedding_type_prefix: str,
) -> int:
    """Delete embeddings whose embedding_type starts with the given prefix.

    Useful for clearing all visual section embeddings before re-storing.
    Returns the number of rows deleted.
    """
    with get_session_local()() as session:
        q = delete(EntityEmbedding).where(
            EntityEmbedding.entity_type == entity_type,
            EntityEmbedding.entity_id == entity_id,
            EntityEmbedding.embedding_type.like(embedding_type_prefix + "%"),
        )
        result = session.execute(q)
        session.commit()
        return result.rowcount


async def delete_entity_embeddings_by_prefix_async(
    *,
    entity_type: str,
    entity_id: int,
    embedding_type_prefix: str,
) -> int:
    return await asyncio.to_thread(
        delete_entity_embeddings_by_prefix,
        entity_type=entity_type,
        entity_id=entity_id,
        embedding_type_prefix=embedding_type_prefix,
    )


def get_embedding_stats() -> dict[str, Any]:
    """Return aggregate stats about stored embeddings (for monitoring)."""
    with get_session_local()() as session:
        total = session.execute(
            select(func.count(EntityEmbedding.id))
        ).scalar() or 0
        by_type = session.execute(
            select(
                EntityEmbedding.embedding_type,
                func.count(EntityEmbedding.id),
            ).group_by(EntityEmbedding.embedding_type)
        ).all()
        return {
            "total_embeddings": total,
            "by_type": {r[0]: r[1] for r in by_type},
        }


async def get_embedding_stats_async() -> dict[str, Any]:
    return await asyncio.to_thread(get_embedding_stats)
