"""ORM models for every application table.

Alembic migrations create the schema; these models mirror it for queries.
Every ``user_id`` references the internal ``users.id``, never an auth provider's identity.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

import numpy as np
import numpy.typing as npt
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

EMBEDDING_DIM = 768

BigId = Annotated[int, mapped_column(BigInteger, primary_key=True)]
Str = Annotated[str, mapped_column(Text)]
TextArray = Annotated[list[str], mapped_column(ARRAY(Text))]
BigIntArray = Annotated[list[int], mapped_column(ARRAY(BigInteger))]
Json = Annotated[dict[str, Any], mapped_column(JSONB)]
Money = Annotated[Decimal, mapped_column(Numeric(10, 5))]
Timestamp = Annotated[datetime, mapped_column(DateTime(timezone=True))]
CreatedAt = Annotated[datetime, mapped_column(DateTime(timezone=True), server_default=func.now())]


def user_fk(ondelete: str = "CASCADE") -> ForeignKey:
    return ForeignKey("users.id", ondelete=ondelete)


def article_fk() -> ForeignKey:
    return ForeignKey("articles.id", ondelete="CASCADE")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("auth_issuer", "auth_subject"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    auth_issuer: Mapped[Str]
    auth_subject: Mapped[Str]
    email: Mapped[Str | None]
    created_at: Mapped[CreatedAt]


Index("ix_users_email_lower", func.lower(User.email))


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[BigId]
    pmid: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    pmcid: Mapped[str | None] = mapped_column(Text, unique=True)
    doi: Mapped[Str | None]
    title: Mapped[Str]
    journal: Mapped[Str | None]
    pub_date: Mapped[date | None]
    entrez_date: Mapped[date | None]
    pub_types: Mapped[TextArray | None]
    mesh_terms: Mapped[TextArray | None]
    license_code: Mapped[Str | None]
    is_retracted: Mapped[bool] = mapped_column(server_default=text("false"))
    has_full_text: Mapped[bool] = mapped_column(server_default=text("false"))
    source_etag: Mapped[Str | None]
    study_meta: Mapped[Json | None]
    evidence_level: Mapped[Str | None]
    publication_status: Mapped[Str | None]
    last_checked_at: Mapped[Timestamp | None]
    created_at: Mapped[CreatedAt]

    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="article",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Chunk.chunk_index",
        lazy="raise",
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        Index("ix_chunks_article_id", "article_id"),
    )

    id: Mapped[BigId]
    article_id: Mapped[int] = mapped_column(BigInteger, article_fk())
    section: Mapped[Str | None]
    chunk_index: Mapped[int | None] = mapped_column(Integer)
    content: Mapped[Str]
    embedding: Mapped[npt.NDArray[np.float32] | None] = mapped_column(Vector(EMBEDDING_DIM))
    embed_model: Mapped[Str]
    tsv: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', content)", persisted=True)
    )

    article: Mapped[Article] = relationship(back_populates="chunks", lazy="raise")


class Trial(Base):
    __tablename__ = "trials"

    nct_id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[Str | None]
    status: Mapped[Str | None]
    phase: Mapped[Str | None]
    enrollment: Mapped[int | None] = mapped_column(Integer)
    conditions: Mapped[TextArray | None]
    interventions: Mapped[TextArray | None]
    start_date: Mapped[date | None]
    completion_date: Mapped[date | None]
    fetched_at: Mapped[CreatedAt]


class SavedSearch(Base):
    __tablename__ = "saved_searches"
    __table_args__ = (CheckConstraint("cadence IN ('weekly', 'monthly')", name="cadence"),)

    id: Mapped[BigId]
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), user_fk())
    topic: Mapped[Str]
    pubmed_query: Mapped[Str]
    cadence: Mapped[Str]
    last_run_at: Mapped[Timestamp | None]
    created_at: Mapped[CreatedAt]


class DeliveredArticle(Base):
    __tablename__ = "delivered_articles"

    saved_search_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("saved_searches.id", ondelete="CASCADE"), primary_key=True
    )
    article_id: Mapped[int] = mapped_column(BigInteger, article_fk(), primary_key=True)
    delivered_at: Mapped[CreatedAt]


class Bookmark(Base):
    __tablename__ = "bookmarks"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), user_fk(), primary_key=True)
    article_id: Mapped[int] = mapped_column(BigInteger, article_fk(), primary_key=True)
    created_at: Mapped[CreatedAt]


class QueryLog(Base):
    __tablename__ = "query_logs"

    id: Mapped[BigId]
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), user_fk("SET NULL"))
    mode: Mapped[Str | None]
    user_query: Mapped[Str | None]
    plan: Mapped[Json | None]
    candidate_pmids: Mapped[BigIntArray | None]
    context_chunk_ids: Mapped[BigIntArray | None]
    answer: Mapped[Json | None]
    evidence_gap: Mapped[bool | None]
    verification: Mapped[Json | None]
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Money | None]
    feedback: Mapped[int | None] = mapped_column(SmallInteger)
    trace_id: Mapped[Str | None]
    created_at: Mapped[CreatedAt]


class UsageDaily(Base):
    __tablename__ = "usage_daily"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), user_fk(), primary_key=True)
    day: Mapped[date] = mapped_column(primary_key=True)
    cost_usd: Mapped[Money] = mapped_column(server_default=text("0"))
    requests: Mapped[int] = mapped_column(Integer, server_default=text("0"))
