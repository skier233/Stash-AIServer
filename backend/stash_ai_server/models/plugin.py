from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import Integer, String, DateTime, Text, Boolean, JSON, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.orm import Mapped, mapped_column
from stash_ai_server.db.session import Base

class PluginMeta(Base):
    __tablename__ = 'plugin_meta'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    required_backend: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default='active')  # active|error|incompatible
    migration_head: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    human_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    server_link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc), nullable=False)


class PluginSource(Base):
    __tablename__ = 'plugin_sources'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc), nullable=False)
    catalog_entries = relationship('PluginCatalog', back_populates='source', cascade='all,delete-orphan')


class PluginCatalog(Base):
    __tablename__ = 'plugin_catalog'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(Integer, ForeignKey('plugin_sources.id', ondelete='CASCADE'), nullable=False, index=True)
    plugin_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    human_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    server_link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    dependencies_json: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)  # {"plugins": [..]}
    manifest_json: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc), nullable=False)
    source = relationship('PluginSource', back_populates='catalog_entries')


class PluginSetting(Base):
    __tablename__ = 'plugin_settings'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plugin_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False, default='string')
    label: Mapped[str | None] = mapped_column(String(150), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_value: Mapped[Any | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    options: Mapped[Any | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    value: Mapped[Any | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now(timezone.utc), nullable=False)
