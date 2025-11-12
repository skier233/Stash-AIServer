from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence, Tuple

from stash_ai_server.recommendations.models import RecommendationRequest

_DEFAULT_CACHE_KEY = "_precomputed_pages"


def resolve_pagination(
    request: RecommendationRequest,
    *,
    default_limit: int = 40,
) -> Tuple[int, int]:
    """Return a sanitized ``(offset, limit)`` pair for the request.

    Values are coerced to non-negative integers with ``default_limit`` providing a
    fallback when the caller does not supply a limit. A zero limit is interpreted as
    "no limit" and left as zero for the caller to handle explicitly.
    """

    raw_offset = request.offset if isinstance(request.offset, int) and request.offset is not None else 0
    if raw_offset < 0:
        offset = 0
    else:
        offset = raw_offset

    raw_limit = request.limit if isinstance(request.limit, int) and request.limit is not None else default_limit
    if raw_limit <= 0:
        limit = 0
    else:
        limit = raw_limit

    return offset, limit


def get_cached_page(
    *,
    ctx: Dict[str, Any],
    cache_key: Any,
    offset: int,
    limit: int,
) -> Tuple[List[Any], int, bool] | None:
    """If a cached result exists, return the paginated slice for ``offset``/``limit``."""

    cache_bucket = ctx.get(_DEFAULT_CACHE_KEY)
    if not cache_bucket:
        return None

    entry = cache_bucket.get(cache_key)
    if not entry:
        return None

    items: Sequence[Any] = entry.get("items") or []
    total = len(items)
    start = min(offset, total)
    end = start + limit if limit > 0 else total
    page = list(items[start:end])
    has_more = end < total
    return page, total, has_more


def store_cache(
    *,
    ctx: Dict[str, Any],
    cache_key: Any,
    items: Sequence[Any],
) -> None:
    """Persist ``items`` in the recommendation context for later pagination reuse."""

    cache_bucket = ctx.setdefault(_DEFAULT_CACHE_KEY, {})
    cache_bucket[cache_key] = {"items": list(items)}


def paginate_items(
    items: Sequence[Any],
    *,
    offset: int,
    limit: int,
) -> Tuple[List[Any], int, bool]:
    """Paginate ``items`` without caching and return ``(page, total, has_more)``."""

    total = len(items)
    start = min(offset, total)
    end = start + limit if limit > 0 else total
    page = list(items[start:end])
    has_more = end < total
    return page, total, has_more
