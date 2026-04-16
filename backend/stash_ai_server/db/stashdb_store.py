"""Store and query functions for StashDB performer reference embeddings.

Follows the same patterns as ``detection_store.py`` — synchronous core with
``_async`` wrappers via ``asyncio.to_thread``.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import unicodedata
from typing import Any, Sequence

import numpy as np
import sqlalchemy as sa
from sqlalchemy import select, update, delete, func
from sqlalchemy.orm import Session

from stash_ai_server.db.session import get_session_local
from stash_ai_server.models.detections import StashDBPerformerRef

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Database encoding detection + text coercion
# ---------------------------------------------------------------------------

# PostgreSQL encoding name → Python codec mapping (common ones)
_PG_TO_PYTHON_CODEC: dict[str, str] = {
    "UTF8": "utf-8",
    "LATIN1": "latin-1",
    "LATIN2": "iso8859-2",
    "LATIN9": "iso8859-15",
    "WIN1250": "cp1250",
    "WIN1251": "cp1251",
    "WIN1252": "cp1252",
    "WIN1253": "cp1253",
    "WIN1254": "cp1254",
    "WIN1255": "cp1255",
    "WIN1256": "cp1256",
    "WIN1257": "cp1257",
    "WIN1258": "cp1258",
    "SQL_ASCII": "ascii",
    "ISO_8859_5": "iso8859-5",
    "ISO_8859_6": "iso8859-6",
    "ISO_8859_7": "iso8859-7",
    "ISO_8859_8": "iso8859-8",
    "KOI8R": "koi8-r",
    "KOI8U": "koi8-u",
    "SJIS": "shift_jis",
    "EUC_JP": "euc_jp",
    "EUC_KR": "euc_kr",
    "BIG5": "big5",
    "GB18030": "gb18030",
}

_db_python_codec: str | None = None  # cached after first detection


def _detect_db_codec() -> str:
    """Detect the PostgreSQL server encoding and return a Python codec name.

    Result is cached for the lifetime of the process.  Returns ``'utf-8'``
    if the encoding cannot be determined or is already UTF-8.
    """
    global _db_python_codec
    if _db_python_codec is not None:
        return _db_python_codec

    try:
        with get_session_local()() as session:
            row = session.execute(sa.text("SHOW server_encoding")).scalar()
            pg_enc = (row or "UTF8").upper().strip()
    except Exception:
        pg_enc = "UTF8"

    _db_python_codec = _PG_TO_PYTHON_CODEC.get(pg_enc, "utf-8")
    if _db_python_codec != "utf-8":
        _log.info(
            "PostgreSQL server encoding is %s (Python codec: %s); "
            "text will be transliterated to fit",
            pg_enc,
            _db_python_codec,
        )
    return _db_python_codec


def _coerce_to_db_encoding(text: str) -> str:
    """Best-effort transliteration of *text* so it survives the DB encoding.

    1. If the DB is UTF-8 the string is returned as-is (fast path).
    2. Otherwise, try encoding to the DB codec.  If that succeeds, return as-is.
    3. For characters that fail, try NFKD decomposition to strip accents / map
       to ASCII equivalents, then replace anything still un-encodable with ``?``.
    """
    codec = _detect_db_codec()
    if codec == "utf-8":
        return text

    # Fast check: can the whole string survive?
    try:
        text.encode(codec)
        return text
    except (UnicodeEncodeError, LookupError):
        pass

    # Character-by-character transliteration
    out: list[str] = []
    for ch in text:
        try:
            ch.encode(codec)
            out.append(ch)
        except (UnicodeEncodeError, LookupError):
            # Try NFKD decomposition (e.g. ō → o + combining macron → keep o)
            decomposed = unicodedata.normalize("NFKD", ch)
            kept = []
            for d in decomposed:
                try:
                    d.encode(codec)
                    kept.append(d)
                except (UnicodeEncodeError, LookupError):
                    pass
            out.append("".join(kept) if kept else "?")
    return "".join(out)


def _sanitize_text(value: str | None) -> str | None:
    """Normalize text imported from external packs.

    Strips invisible unicode formatting/control characters such as U+2063
    that can cause driver/encoding issues on some Windows setups while
    preserving ordinary Unicode names.  Then coerces the result so it is
    representable in the PostgreSQL server's encoding.
    """
    if value is None:
        return None

    normalized = unicodedata.normalize("NFKC", value)
    cleaned = "".join(
        ch for ch in normalized
        if unicodedata.category(ch) not in {"Cf", "Cc", "Cs"}
    )
    result = cleaned.strip() or None
    if result is not None:
        result = _coerce_to_db_encoding(result)
    return result


def _sanitize_aliases(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    cleaned = []
    for item in values:
        text = _sanitize_text(item)
        if text:
            cleaned.append(text)
    return cleaned


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def upsert_stashdb_ref(
    session: Session,
    *,
    stashdb_id: str,
    name: str,
    centroid: list[float],
    embedder: str,
    disambiguation: str | None = None,
    aliases: list[str] | None = None,
    sample_count: int = 0,
    quality_score: float | None = None,
    source_endpoint: str | None = None,
    pack_id: str | None = None,
    local_performer_id: int | None = None,
    image_url: str | None = None,
) -> tuple[StashDBPerformerRef, bool]:
    """Insert or update a StashDB performer reference.

    Returns ``(ref, created)`` where *created* is True for inserts and
    False for updates.
    """
    name = _sanitize_text(name) or stashdb_id
    disambiguation = _sanitize_text(disambiguation)
    aliases = _sanitize_aliases(aliases)
    source_endpoint = _sanitize_text(source_endpoint)
    pack_id = _sanitize_text(pack_id)

    existing = session.execute(
        select(StashDBPerformerRef).where(StashDBPerformerRef.stashdb_id == stashdb_id)
    ).scalar_one_or_none()

    if existing is not None:
        existing.name = name
        existing.centroid = centroid
        existing.embedder = embedder
        existing.sample_count = sample_count
        existing.updated_at = dt.datetime.now(dt.timezone.utc)
        if disambiguation is not None:
            existing.disambiguation = disambiguation
        if aliases is not None:
            existing.aliases = aliases
        if quality_score is not None:
            existing.quality_score = quality_score
        if source_endpoint is not None:
            # Merge extra endpoints — keep existing source_endpoint, add new ones
            if existing.source_endpoint and existing.source_endpoint != source_endpoint:
                extras = existing.extra_endpoints or []
                if source_endpoint not in extras and source_endpoint != existing.source_endpoint:
                    extras.append(source_endpoint)
                    existing.extra_endpoints = extras
            else:
                existing.source_endpoint = source_endpoint
        if pack_id is not None:
            existing.pack_id = pack_id
        if local_performer_id is not None:
            existing.local_performer_id = local_performer_id
        if image_url is not None:
            existing.image_url = image_url
        session.flush()
        return existing, False

    ref = StashDBPerformerRef(
        stashdb_id=stashdb_id,
        name=name,
        disambiguation=disambiguation,
        aliases=aliases,
        centroid=centroid,
        sample_count=sample_count,
        quality_score=quality_score,
        embedder=embedder,
        source_endpoint=source_endpoint,
        pack_id=pack_id,
        local_performer_id=local_performer_id,
        image_url=image_url,
    )
    session.add(ref)
    session.flush()
    return ref, True


def bulk_upsert_stashdb_refs(
    rows: list[dict],
    *,
    on_progress: Any | None = None,
) -> tuple[int, int, int]:
    """Bulk insert/update StashDB performer references using ON CONFLICT.

    Each dict in *rows* must contain at minimum ``stashdb_id``, ``name``,
    ``centroid`` (list[float]), and ``embedder``.  Optional keys:
    ``disambiguation``, ``aliases``, ``sample_count``, ``quality_score``,
    ``source_endpoint``, ``pack_id``, ``local_performer_id``, ``image_url``.

    Text fields should already be sanitized by the caller.  If *on_progress*
    is provided it is called as ``on_progress(done, total)`` after each batch.

    Returns ``(imported, updated, errors)``.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    if not rows:
        return 0, 0, 0

    imported = 0
    updated = 0
    errors = 0
    batch_size = 1000
    now = dt.datetime.now(dt.timezone.utc)
    total_rows = len(rows)

    with get_session_local()() as session:
        for batch_start in range(0, total_rows, batch_size):
            batch = rows[batch_start : batch_start + batch_size]
            values = []
            for r in batch:
                try:
                    values.append({
                        "stashdb_id": r["stashdb_id"],
                        "name": r["name"],
                        "disambiguation": r.get("disambiguation"),
                        "aliases": r.get("aliases"),
                        "centroid": r["centroid"],
                        "sample_count": r.get("sample_count", 0),
                        "quality_score": r.get("quality_score"),
                        "embedder": r["embedder"],
                        "source_endpoint": r.get("source_endpoint"),
                        "pack_id": r.get("pack_id"),
                        "local_performer_id": r.get("local_performer_id"),
                        "image_url": r.get("image_url"),
                        "created_at": now,
                        "updated_at": now,
                    })
                except Exception:
                    errors += 1
                    continue

            if not values:
                if on_progress:
                    on_progress(min(batch_start + batch_size, total_rows), total_rows)
                continue

            try:
                # Pre-count existing rows in this batch for insert vs update tally
                batch_ids = [v["stashdb_id"] for v in values]
                existing_count = session.execute(
                    select(func.count(StashDBPerformerRef.id))
                    .where(StashDBPerformerRef.stashdb_id.in_(batch_ids))
                ).scalar() or 0

                stmt = pg_insert(StashDBPerformerRef).values(values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["stashdb_id"],
                    set_={
                        "name": stmt.excluded.name,
                        "centroid": stmt.excluded.centroid,
                        "embedder": stmt.excluded.embedder,
                        "sample_count": stmt.excluded.sample_count,
                        "updated_at": stmt.excluded.updated_at,
                        "disambiguation": func.coalesce(
                            stmt.excluded.disambiguation,
                            StashDBPerformerRef.disambiguation,
                        ),
                        "aliases": func.coalesce(
                            stmt.excluded.aliases,
                            StashDBPerformerRef.aliases,
                        ),
                        "quality_score": func.coalesce(
                            stmt.excluded.quality_score,
                            StashDBPerformerRef.quality_score,
                        ),
                        "source_endpoint": func.coalesce(
                            stmt.excluded.source_endpoint,
                            StashDBPerformerRef.source_endpoint,
                        ),
                        "pack_id": func.coalesce(
                            stmt.excluded.pack_id,
                            StashDBPerformerRef.pack_id,
                        ),
                        "local_performer_id": func.coalesce(
                            stmt.excluded.local_performer_id,
                            StashDBPerformerRef.local_performer_id,
                        ),
                        "image_url": func.coalesce(
                            stmt.excluded.image_url,
                            StashDBPerformerRef.image_url,
                        ),
                    },
                )
                session.execute(stmt)
                session.commit()

                batch_imported = len(values) - existing_count
                imported += batch_imported
                updated += existing_count
            except Exception:
                _log.exception("Bulk upsert batch failed (offset %d)", batch_start)
                session.rollback()
                errors += len(values)

            if on_progress:
                on_progress(min(batch_start + batch_size, total_rows), total_rows)

    return imported, updated, errors


def get_ref_by_id(ref_id: int) -> StashDBPerformerRef | None:
    with get_session_local()() as session:
        return session.get(StashDBPerformerRef, ref_id)


def get_ref_by_stashdb_id(stashdb_id: str) -> StashDBPerformerRef | None:
    with get_session_local()() as session:
        return session.execute(
            select(StashDBPerformerRef).where(StashDBPerformerRef.stashdb_id == stashdb_id)
        ).scalar_one_or_none()


def delete_ref(ref_id: int) -> bool:
    with get_session_local()() as session:
        result = session.execute(
            delete(StashDBPerformerRef).where(StashDBPerformerRef.id == ref_id)
        )
        session.commit()
        return result.rowcount > 0


async def delete_ref_async(ref_id: int) -> bool:
    return await asyncio.to_thread(delete_ref, ref_id)


def delete_pack(pack_id: str) -> int:
    """Delete all refs belonging to *pack_id*. Returns count of deleted rows."""
    with get_session_local()() as session:
        result = session.execute(
            delete(StashDBPerformerRef).where(StashDBPerformerRef.pack_id == pack_id)
        )
        session.commit()
        return result.rowcount


async def delete_pack_async(pack_id: str) -> int:
    return await asyncio.to_thread(delete_pack, pack_id)


def set_local_performer_id(ref_id: int, performer_id: int) -> None:
    """Store the local Stash performer ID once the performer is created."""
    with get_session_local()() as session:
        session.execute(
            update(StashDBPerformerRef)
            .where(StashDBPerformerRef.id == ref_id)
            .values(
                local_performer_id=performer_id,
                updated_at=dt.datetime.now(dt.timezone.utc),
            )
        )
        session.commit()


async def set_local_performer_id_async(ref_id: int, performer_id: int) -> None:
    await asyncio.to_thread(set_local_performer_id, ref_id, performer_id)


def clear_local_performer_id(ref_id: int) -> None:
    """Clear a stale local performer link from a StashDB ref."""
    with get_session_local()() as session:
        session.execute(
            update(StashDBPerformerRef)
            .where(StashDBPerformerRef.id == ref_id)
            .values(
                local_performer_id=None,
                updated_at=dt.datetime.now(dt.timezone.utc),
            )
        )
        session.commit()


async def clear_local_performer_id_async(ref_id: int) -> None:
    await asyncio.to_thread(clear_local_performer_id, ref_id)


# ---------------------------------------------------------------------------
# Vector search
# ---------------------------------------------------------------------------

def find_nearest_stashdb_ref(
    embedding: list[float] | np.ndarray,
    *,
    limit: int = 5,
    min_similarity: float = 0.55,
    embedder: str | None = None,
) -> list[tuple[int, str, str, float]]:
    """Return ``[(ref_id, stashdb_id, name, similarity), ...]`` ordered by similarity desc.

    If *embedder* is specified, only refs generated with that embedder are searched.
    """
    vec = embedding.tolist() if isinstance(embedding, np.ndarray) else list(embedding)

    with get_session_local()() as session:
        distance_expr = StashDBPerformerRef.centroid.cosine_distance(vec)
        similarity_expr = (1 - distance_expr).label("similarity")

        conditions = [similarity_expr >= min_similarity]
        if embedder:
            conditions.append(StashDBPerformerRef.embedder == embedder)

        stmt = (
            select(
                StashDBPerformerRef.id,
                StashDBPerformerRef.stashdb_id,
                StashDBPerformerRef.name,
                similarity_expr,
            )
            .where(*conditions)
            .order_by(distance_expr)
            .limit(limit)
        )
        rows = session.execute(stmt).all()
        return [(int(r[0]), r[1], r[2], float(r[3])) for r in rows]


async def find_nearest_stashdb_ref_async(
    embedding: list[float] | np.ndarray, **kwargs: Any
) -> list[tuple[int, str, str, float]]:
    return await asyncio.to_thread(find_nearest_stashdb_ref, embedding, **kwargs)


# ---------------------------------------------------------------------------
# Listing / stats
# ---------------------------------------------------------------------------

def list_refs(
    *,
    search: str | None = None,
    pack_id: str | None = None,
    has_local_performer: bool | None = None,
    page: int = 1,
    per_page: int = 50,
    sort: str = "name",
    sort_dir: str = "asc",
) -> tuple[list[StashDBPerformerRef], int]:
    """Return a paginated list of StashDB performer refs + total count."""
    with get_session_local()() as session:
        base = select(StashDBPerformerRef)
        count_base = select(func.count(StashDBPerformerRef.id))

        conditions = []
        if search:
            pattern = f"%{search}%"
            conditions.append(
                sa.or_(
                    StashDBPerformerRef.name.ilike(pattern),
                    StashDBPerformerRef.stashdb_id.ilike(pattern),
                )
            )
        if pack_id:
            conditions.append(StashDBPerformerRef.pack_id == pack_id)
        if has_local_performer is True:
            conditions.append(StashDBPerformerRef.local_performer_id.isnot(None))
        elif has_local_performer is False:
            conditions.append(StashDBPerformerRef.local_performer_id.is_(None))

        if conditions:
            base = base.where(*conditions)
            count_base = count_base.where(*conditions)

        total = session.execute(count_base).scalar() or 0

        sort_col_map = {
            "name": StashDBPerformerRef.name,
            "sample_count": StashDBPerformerRef.sample_count,
            "quality_score": StashDBPerformerRef.quality_score,
            "created_at": StashDBPerformerRef.created_at,
            "updated_at": StashDBPerformerRef.updated_at,
        }
        col = sort_col_map.get(sort, StashDBPerformerRef.name)
        order = col.desc() if sort_dir == "desc" else col.asc()

        offset = (page - 1) * per_page
        rows = session.execute(
            base.order_by(order).offset(offset).limit(per_page)
        ).scalars().all()

        # Eagerly detach so they survive session close
        for r in rows:
            session.expunge(r)

        return list(rows), total


async def list_refs_async(**kwargs: Any) -> tuple[list[StashDBPerformerRef], int]:
    return await asyncio.to_thread(list_refs, **kwargs)


def list_packs() -> list[dict]:
    """Return summary of each imported pack."""
    with get_session_local()() as session:
        stmt = (
            select(
                StashDBPerformerRef.pack_id,
                StashDBPerformerRef.embedder,
                StashDBPerformerRef.source_endpoint,
                func.count(StashDBPerformerRef.id).label("count"),
                func.min(StashDBPerformerRef.created_at).label("first_imported"),
                func.max(StashDBPerformerRef.updated_at).label("last_updated"),
            )
            .group_by(
                StashDBPerformerRef.pack_id,
                StashDBPerformerRef.embedder,
                StashDBPerformerRef.source_endpoint,
            )
            .order_by(func.max(StashDBPerformerRef.updated_at).desc())
        )
        rows = session.execute(stmt).all()
        return [
            {
                "pack_id": r[0],
                "embedder": r[1],
                "source_endpoint": r[2],
                "count": r[3],
                "first_imported": r[4].isoformat() if r[4] else None,
                "last_updated": r[5].isoformat() if r[5] else None,
            }
            for r in rows
        ]


async def list_packs_async() -> list[dict]:
    return await asyncio.to_thread(list_packs)


def get_stats() -> dict:
    """Return aggregate statistics about imported StashDB refs."""
    with get_session_local()() as session:
        total = session.execute(
            select(func.count(StashDBPerformerRef.id))
        ).scalar() or 0
        with_local = session.execute(
            select(func.count(StashDBPerformerRef.id))
            .where(StashDBPerformerRef.local_performer_id.isnot(None))
        ).scalar() or 0
        embedders = session.execute(
            select(StashDBPerformerRef.embedder).distinct()
        ).scalars().all()
        return {
            "total_refs": total,
            "with_local_performer": with_local,
            "without_local_performer": total - with_local,
            "embedders": list(embedders),
        }


async def get_stats_async() -> dict:
    return await asyncio.to_thread(get_stats)


# ---------------------------------------------------------------------------
# Post-import backfill
# ---------------------------------------------------------------------------

def backfill_stashdb_matches() -> int:
    """Match existing face clusters that have no StashDB suggestion yet.

    Iterates all clusters with ``status='unidentified'`` and
    ``stashdb_match_id IS NULL``, runs an ANN query for each against
    the StashDB ref centroids, and stores the best match.

    Clusters whose match exceeds the ``stashdb_auto_link_threshold``
    plugin setting are automatically linked to the matching performer
    (or a new one is created).

    Returns the number of clusters that received a new match.
    """
    from stash_ai_server.models.detections import FaceCluster

    # Read the auto-link threshold from plugin settings
    auto_link_threshold = 0.70
    min_similarity = 0.60
    try:
        from stash_ai_server.models.plugin import PluginSetting
        with get_session_local()() as s:
            row = s.execute(
                select(PluginSetting.value, PluginSetting.default_value)
                .where(
                    PluginSetting.plugin_name == "skier_aitagging",
                    PluginSetting.key == "stashdb_auto_link_threshold",
                )
            ).first()
            if row:
                raw = row[0] if row[0] is not None else row[1]
                if raw is not None:
                    auto_link_threshold = float(raw)
    except Exception:
        _log.debug("Could not read stashdb_auto_link_threshold", exc_info=True)
    # Use the lower of the auto-link threshold and 0.60 as the minimum
    # similarity for storing suggestions.
    min_similarity = min(min_similarity, auto_link_threshold) if auto_link_threshold > 0 else min_similarity

    # Load unmatched cluster IDs + centroids in one query
    with get_session_local()() as session:
        rows = session.execute(
            select(FaceCluster.id, FaceCluster.centroid)
            .where(
                FaceCluster.status == "unidentified",
                FaceCluster.stashdb_match_id.is_(None),
                FaceCluster.centroid.isnot(None),
            )
        ).all()

    if not rows:
        return 0

    matched = 0
    batch: list[tuple[int, int, float, str, str]] = []  # (cluster_id, ref_id, similarity, stashdb_id, name)

    for cluster_id, centroid in rows:
        centroid_list = list(centroid) if not isinstance(centroid, list) else centroid
        results = find_nearest_stashdb_ref(centroid_list, limit=1, min_similarity=min_similarity)
        if results:
            ref_id, stashdb_id, name, similarity = results[0]
            batch.append((cluster_id, ref_id, similarity, stashdb_id, name))

    # Apply matches in batches of 500
    if batch:
        from stash_ai_server.models.detections import FaceCluster as FC  # noqa: F811

        for i in range(0, len(batch), 500):
            chunk = batch[i : i + 500]
            with get_session_local()() as session:
                for cluster_id, ref_id, similarity, _sid, _name in chunk:
                    session.execute(
                        sa.update(FC)
                        .where(FC.id == cluster_id)
                        .values(
                            stashdb_match_id=ref_id,
                            stashdb_match_score=similarity,
                        )
                    )
                session.commit()
            matched += len(chunk)

    # Auto-link clusters that exceed the threshold
    auto_linked = 0
    if auto_link_threshold > 0:
        for cluster_id, ref_id, similarity, stashdb_id, name in batch:
            if similarity >= auto_link_threshold:
                try:
                    from plugins.skier_aitagging.face_processor import _auto_link_cluster_to_stashdb
                    _auto_link_cluster_to_stashdb(cluster_id, ref_id, stashdb_id, name, similarity)
                    auto_linked += 1
                except Exception:
                    _log.debug("Backfill auto-link failed for cluster %d", cluster_id, exc_info=True)

    _log.info(
        "StashDB backfill: matched %d / %d unmatched cluster(s), auto-linked %d",
        matched, len(rows), auto_linked,
    )
    return matched


async def backfill_stashdb_matches_async() -> int:
    return await asyncio.to_thread(backfill_stashdb_matches)
