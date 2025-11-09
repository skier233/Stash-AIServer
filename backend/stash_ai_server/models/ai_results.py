from __future__ import annotations

import datetime as dt

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from stash_ai_server.db.session import Base


class AIModel(Base):
    __tablename__ = "ai_models"

    id: Mapped[int] = mapped_column(primary_key=True)
    service: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    plugin_name: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    model_id: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    name: Mapped[str] = mapped_column(sa.String(150), nullable=False)
    version: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    model_type: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)
    categories: Mapped[list[str] | None] = mapped_column(sa.JSON(none_as_null=True), nullable=True)
    extra: Mapped[dict | None] = mapped_column(sa.JSON(none_as_null=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        sa.DateTime, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
    )

    runs: Mapped[list["AIModelRunModel"]] = relationship("AIModelRunModel", back_populates="model")

    __table_args__ = (
        sa.UniqueConstraint("service", "model_id", "name", name="uq_ai_model_service_model_name"),
        sa.Index("ix_ai_models_service", "service"),
    )


class AIModelRun(Base):
    __tablename__ = "ai_model_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    service: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    plugin_name: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    entity_type: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    entity_id: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False, server_default="completed")
    input_params: Mapped[dict | None] = mapped_column(sa.JSON(none_as_null=True), nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(
        sa.DateTime, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
    )
    completed_at: Mapped[dt.datetime | None] = mapped_column(sa.DateTime, nullable=True)
    result_metadata: Mapped[dict | None] = mapped_column(sa.JSON(none_as_null=True), nullable=True)

    models: Mapped[list[AIModelRunModel]] = relationship(
        "AIModelRunModel", back_populates="run", cascade="all, delete-orphan"
    )
    timespans: Mapped[list[AIResultTimespan]] = relationship(
        "AIResultTimespan", back_populates="run", cascade="all, delete-orphan"
    )
    aggregates: Mapped[list[AIResultAggregate]] = relationship(
        "AIResultAggregate", back_populates="run", cascade="all, delete-orphan"
    )

    __table_args__ = (
        sa.Index("ix_ai_model_runs_entity", "entity_type", "entity_id"),
        sa.Index("ix_ai_model_runs_service_entity", "service", "entity_type", "entity_id"),
    )


class AIModelRunModel(Base):
    __tablename__ = "ai_model_run_models"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(sa.ForeignKey("ai_model_runs.id", ondelete="CASCADE"), nullable=False)
    model_id: Mapped[int | None] = mapped_column(sa.ForeignKey("ai_models.id", ondelete="SET NULL"), nullable=True)
    input_params: Mapped[dict | None] = mapped_column(sa.JSON(none_as_null=True), nullable=True)
    frame_interval: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
    )

    run: Mapped[AIModelRun] = relationship("AIModelRun", back_populates="models")  # type: ignore  # noqa: F821
    model: Mapped[AIModel | None] = relationship("AIModel", back_populates="runs")

    __table_args__ = (
        sa.Index("ix_ai_run_models_run", "run_id"),
        sa.Index("ix_ai_run_models_model", "model_id"),
    )


class AIResultTimespan(Base):
    __tablename__ = "ai_result_timespans"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(sa.ForeignKey("ai_model_runs.id", ondelete="CASCADE"), nullable=False)
    entity_type: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    entity_id: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    payload_type: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    category: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    str_value: Mapped[str | None] = mapped_column(sa.String(150), nullable=True)
    value_id: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    start_s: Mapped[float] = mapped_column(sa.Float, nullable=False)
    end_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    value_json: Mapped[dict | None] = mapped_column(sa.JSON(none_as_null=True), nullable=True)

    run: Mapped[AIModelRun] = relationship("AIModelRun", back_populates="timespans")

    __table_args__ = (
        sa.Index("ix_ai_timespans_entity", "entity_type", "entity_id"),
        sa.Index("ix_ai_timespans_run", "run_id"),
        sa.Index("ix_ai_timespans_payload", "payload_type", "category", "str_value"),
        sa.Index("ix_ai_timespans_start", "entity_type", "entity_id", "start_s"),
    )


class AIResultAggregate(Base):
    __tablename__ = "ai_result_aggregates"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(sa.ForeignKey("ai_model_runs.id", ondelete="CASCADE"), nullable=False)
    entity_type: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    entity_id: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    payload_type: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    category: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    str_value: Mapped[str | None] = mapped_column(sa.String(150), nullable=True)
    value_id: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    metric: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    value_float: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    value_json: Mapped[dict | None] = mapped_column(sa.JSON(none_as_null=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        sa.DateTime, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
    )

    run: Mapped[AIModelRun] = relationship("AIModelRun", back_populates="aggregates")

    __table_args__ = (
        sa.Index("ix_ai_aggregates_entity", "entity_type", "entity_id"),
        sa.Index("ix_ai_aggregates_payload", "payload_type", "str_value", "metric"),
    )


__all__ = [
    "AIModel",
    "AIModelRun",
    "AIModelRunModel",
    "AIResultTimespan",
    "AIResultAggregate",
]
