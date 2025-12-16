from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class HealthStatus(str, Enum):
    OK = "ok"
    WARN = "warn"
    ERROR = "error"


class HealthComponent(BaseModel):
    status: HealthStatus = Field(..., description="Component health state")
    message: str = Field(..., description="Primary user-facing summary")
    details: Dict[str, Any] | None = Field(
        default=None, description="Additional metadata to help with troubleshooting"
    )
    latency_ms: float | None = Field(
        default=None, description="Probe execution time in milliseconds"
    )


class SystemHealthSnapshot(BaseModel):
    status: HealthStatus = Field(..., description="Overall health derived from components")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when snapshot was generated",
    )
    stash_api: HealthComponent
    database: HealthComponent

    class Config:
        use_enum_values = True
