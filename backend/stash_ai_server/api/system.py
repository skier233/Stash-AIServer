from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Tuple

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from stash_ai_server.api.version import get_version_payload
from stash_ai_server.core.api_key import require_shared_api_key
from stash_ai_server.core.system_settings import get_value as sys_get
from stash_ai_server.db.session import get_db
from stash_ai_server.schemas.health import HealthComponent, HealthStatus, SystemHealthSnapshot
from stash_ai_server.utils import stash_db
from stash_ai_server.utils.path_mutation import mutate_path_for_backend
from stash_ai_server.utils.stash_api import stash_api

router = APIRouter(prefix="/system", tags=["system"], dependencies=[Depends(require_shared_api_key)])

_STATUS_ORDER = {
    HealthStatus.OK: 0,
    HealthStatus.WARN: 1,
    HealthStatus.ERROR: 2,
}


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


async def _probe_stash_api() -> HealthComponent:
    start = time.perf_counter()
    configured_url = (stash_api.stash_url or "").strip()
    effective_url = getattr(stash_api, "_effective_url", None)
    api_key = (stash_api.api_key or "").strip()

    details = {
        "configured_url": configured_url or None,
        "effective_url": effective_url or None,
        "api_key_configured": bool(api_key),
    }

    if not configured_url:
        return HealthComponent(
            status=HealthStatus.WARN,
            message="Stash URL not configured",
            details=details,
            latency_ms=_elapsed_ms(start),
        )

    interface = stash_api.stash_interface
    if interface is None:
        return HealthComponent(
            status=HealthStatus.ERROR,
            message="Unable to initialize Stash API client",
            details=details,
            latency_ms=_elapsed_ms(start),
        )

    def _probe() -> str | None:
        try:
            interface.find_tags(filter={"per_page": 1, "page": 1}, fragment="id")
            return None
        except Exception as exc:  # pragma: no cover - defensive network check
            return str(exc) or exc.__class__.__name__

    error = await asyncio.to_thread(_probe)
    latency = _elapsed_ms(start)

    if error:
        details["last_error"] = error
        return HealthComponent(
            status=HealthStatus.ERROR,
            message="Failed to reach Stash API",
            details=details,
            latency_ms=latency,
        )

    status = HealthStatus.OK
    message = "Connected to Stash API"
    if not api_key:
        status = HealthStatus.WARN
        message = "Connected to Stash API (API key not configured)"
    return HealthComponent(status=status, message=message, details=details, latency_ms=latency)


async def _probe_stash_database() -> HealthComponent:
    start = time.perf_counter()
    raw_setting = sys_get("STASH_DB_PATH")
    configured_path = ""
    if raw_setting is not None:
        configured_path = str(raw_setting).strip()

    mutated_path: str | None = None
    resolved_path: Path | None = None
    path_exists: bool | None = None
    mutation_error: str | None = None

    if configured_path:
        try:
            mutated_path = mutate_path_for_backend(configured_path)
            resolved_path = Path(mutated_path).expanduser()
            try:
                resolved_path = resolved_path.resolve(strict=False)
            except Exception:  # pragma: no cover - best effort
                pass
            path_exists = resolved_path.exists()
        except Exception as exc:  # pragma: no cover - defensive path handling
            mutation_error = str(exc) or exc.__class__.__name__

    def _attempt() -> Tuple[bool, str | None]:
        factory = stash_db.get_stash_sessionmaker()
        if factory is None:
            return False, "Stash database is not configured or unavailable"
        try:
            with factory() as session:
                session.execute(sa.text("SELECT 1"))
            return True, None
        except Exception as exc:  # pragma: no cover - defensive DB probe
            return False, str(exc) or exc.__class__.__name__

    ok, error = await asyncio.to_thread(_attempt)
    latency = _elapsed_ms(start)

    details = {
        "configured_path": configured_path or None,
        "mutated_path": mutated_path or None,
        "resolved_path": str(resolved_path) if resolved_path else None,
        "path_exists": path_exists,
    }
    if mutation_error:
        details["mutation_error"] = mutation_error
    if error:
        details["last_error"] = error

    if not configured_path:
        status = HealthStatus.WARN
        message = "Stash database path not configured"
    elif path_exists is False:
        status = HealthStatus.ERROR
        message = "Configured database path was not found"
    elif not ok:
        status = HealthStatus.ERROR
        message = "Failed to open Stash database"
    else:
        status = HealthStatus.OK
        message = "Stash database accessible"

    return HealthComponent(status=status, message=message, details=details, latency_ms=latency)


@router.get("/health", response_model=SystemHealthSnapshot)
async def get_system_health(db: Session = Depends(get_db)) -> SystemHealthSnapshot:
    stash_component, db_component = await asyncio.gather(
        _probe_stash_api(), _probe_stash_database()
    )

    overall = stash_component.status
    for status in (db_component.status,):
        if _STATUS_ORDER[status] > _STATUS_ORDER[overall]:
            overall = status

    version_payload = get_version_payload(db)

    return SystemHealthSnapshot(
        status=overall,
        stash_api=stash_component,
        database=db_component,
        backend_version=version_payload.get('version'),
        db_alembic_head=version_payload.get('db_alembic_head'),
        version_payload=version_payload,
    )
