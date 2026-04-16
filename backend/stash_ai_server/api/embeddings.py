"""API endpoints for scene/entity content embeddings.

Provides CRUD operations and similarity search for embeddings stored
via the audio and (future) visual pipelines.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from stash_ai_server.core.api_key import require_shared_api_key
from stash_ai_server.db.embedding_store import (
    get_entity_embeddings_async,
    find_similar_entities_async,
    delete_entity_embeddings_async,
    get_embedding_stats_async,
)

router = APIRouter(
    prefix="/embeddings",
    tags=["embeddings"],
    dependencies=[Depends(require_shared_api_key)],
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class EmbeddingResponse(BaseModel):
    id: int
    entity_type: str
    entity_id: int
    embedding_type: str
    dim: int
    embedder: str
    norm: float
    sample_count: int
    start_time: float | None = None
    end_time: float | None = None
    metadata: dict[str, Any] | None = None


class SimilarEntityResponse(BaseModel):
    entity_type: str
    entity_id: int
    similarity: float = Field(description="Cosine similarity (1 = identical, 0 = orthogonal)")
    embedding_type: str


class SimilarityQuery(BaseModel):
    entity_type: str = "scene"
    entity_id: int
    embedding_type: str
    limit: int = Field(default=50, ge=1, le=500)


class SimilarityByVectorQuery(BaseModel):
    entity_type: str = "scene"
    embedding_type: str
    vector: list[float]
    limit: int = Field(default=50, ge=1, le=500)
    exclude_entity_id: int | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/entity/{entity_type}/{entity_id}", response_model=list[EmbeddingResponse])
async def get_entity_embeddings_endpoint(
    entity_type: str,
    entity_id: int,
    embedding_type: str | None = None,
):
    """Get all embeddings stored for an entity."""
    results = await get_entity_embeddings_async(
        entity_type=entity_type,
        entity_id=entity_id,
        embedding_type=embedding_type,
    )
    return [
        EmbeddingResponse(
            id=r.id,
            entity_type=r.entity_type,
            entity_id=r.entity_id,
            embedding_type=r.embedding_type,
            dim=r.dim,
            embedder=r.embedder,
            norm=r.norm,
            sample_count=r.sample_count,
            start_time=r.start_time,
            end_time=r.end_time,
            metadata=r.metadata,
        )
        for r in results
    ]


@router.post("/similar", response_model=list[SimilarEntityResponse])
async def find_similar_by_entity(query: SimilarityQuery):
    """Find entities with similar embeddings to the given entity.

    Looks up the stored embedding for the specified entity and type,
    then performs a cosine similarity search.
    """
    source_embs = await get_entity_embeddings_async(
        entity_type=query.entity_type,
        entity_id=query.entity_id,
        embedding_type=query.embedding_type,
    )
    if not source_embs:
        raise HTTPException(
            status_code=404,
            detail=f"No {query.embedding_type} embedding found for {query.entity_type}/{query.entity_id}",
        )
    source = source_embs[0]
    results = await find_similar_entities_async(
        embedding_type=query.embedding_type,
        query_vector=source.embedding,
        limit=query.limit + 1,  # +1 to exclude self
        entity_type=query.entity_type,
    )
    return [
        SimilarEntityResponse(
            entity_type=r.entity_type,
            entity_id=r.entity_id,
            similarity=round(1.0 - r.distance, 6),
            embedding_type=r.embedding_type,
        )
        for r in results
        if r.entity_id != query.entity_id
    ][:query.limit]


@router.post("/similar_by_vector", response_model=list[SimilarEntityResponse])
async def find_similar_by_vector(query: SimilarityByVectorQuery):
    """Find entities similar to an arbitrary embedding vector."""
    results = await find_similar_entities_async(
        embedding_type=query.embedding_type,
        query_vector=query.vector,
        limit=query.limit,
        exclude_entity_id=query.exclude_entity_id,
        entity_type=query.entity_type,
    )
    return [
        SimilarEntityResponse(
            entity_type=r.entity_type,
            entity_id=r.entity_id,
            similarity=round(1.0 - r.distance, 6),
            embedding_type=r.embedding_type,
        )
        for r in results
    ]


@router.delete("/entity/{entity_type}/{entity_id}")
async def delete_entity_embeddings_endpoint(
    entity_type: str,
    entity_id: int,
    embedding_type: str | None = None,
):
    """Delete embeddings for an entity (optionally filtered by type)."""
    deleted = await delete_entity_embeddings_async(
        entity_type=entity_type,
        entity_id=entity_id,
        embedding_type=embedding_type,
    )
    return {"deleted": deleted}


@router.get("/stats")
async def embedding_stats():
    """Return aggregate stats about stored embeddings."""
    return await get_embedding_stats_async()
