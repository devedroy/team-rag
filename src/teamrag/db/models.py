"""SQLAlchemy 2.x ORM models for TeamRag."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    page_title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_updated: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )

    chunks: Mapped[list[Chunk]] = relationship(
        "Chunk", back_populates="source", cascade="all, delete-orphan"
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        sa.UniqueConstraint("source_id", "chunk_index", name="uq_chunks_source_chunk_index"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_metadata: Mapped[Optional[dict]] = mapped_column(
        "metadata", postgresql.JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )

    source: Mapped[Source] = relationship("Source", back_populates="chunks")
    acl_tags: Mapped[list[AclTag]] = relationship(
        "AclTag", back_populates="chunk", cascade="all, delete-orphan"
    )


class AclTag(Base):
    __tablename__ = "acl_tags"

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("chunks.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    tag: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)

    chunk: Mapped[Chunk] = relationship("Chunk", back_populates="acl_tags")


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        sa.Index("ix_audit_log_queried_at", "queried_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    caller_id: Mapped[str] = mapped_column(Text, nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    acl_tags_applied: Mapped[Optional[list[str]]] = mapped_column(
        postgresql.ARRAY(Text), nullable=True
    )
    result_count: Mapped[int] = mapped_column(Integer, nullable=False)
    queried_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
