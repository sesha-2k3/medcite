"""Initial schema.

Revision ID: 0001
Revises:
Create Date: 2026-10-13

Self-contained by design: migrations never import application models, so later model
changes cannot rewrite history.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 768
UUID = postgresql.UUID(as_uuid=True)
TEXT_ARRAY = postgresql.ARRAY(sa.Text())
BIGINT_ARRAY = postgresql.ARRAY(sa.BigInteger())
MONEY = sa.Numeric(10, 5)

TABLES_IN_DEPENDENCY_ORDER = (
    "users",
    "articles",
    "chunks",
    "trials",
    "saved_searches",
    "delivered_articles",
    "bookmarks",
    "query_logs",
    "usage_daily",
)


def timestamp_now(name: str) -> sa.Column[datetime]:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def fk(column: str, table: str, target: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{target}.id"], name=f"fk_{table}_{column}_{target}", ondelete=ondelete
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("auth_issuer", sa.Text(), nullable=False),
        sa.Column("auth_subject", sa.Text(), nullable=False),
        sa.Column("email", sa.Text()),
        timestamp_now("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint(
            "auth_issuer", "auth_subject", name="uq_users_auth_issuer_auth_subject"
        ),
    )
    op.create_index("ix_users_email_lower", "users", [sa.text("lower(email)")])

    op.create_table(
        "articles",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pmid", sa.BigInteger()),
        sa.Column("pmcid", sa.Text()),
        sa.Column("doi", sa.Text()),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("journal", sa.Text()),
        sa.Column("pub_date", sa.Date()),
        sa.Column("entrez_date", sa.Date()),
        sa.Column("pub_types", TEXT_ARRAY),
        sa.Column("mesh_terms", TEXT_ARRAY),
        sa.Column("license_code", sa.Text()),
        sa.Column("is_retracted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("has_full_text", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("source_etag", sa.Text()),
        sa.Column("study_meta", postgresql.JSONB()),
        sa.Column("evidence_level", sa.Text()),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        timestamp_now("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_articles"),
        sa.UniqueConstraint("pmid", name="uq_articles_pmid"),
        sa.UniqueConstraint("pmcid", name="uq_articles_pmcid"),
    )

    op.create_table(
        "chunks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("article_id", sa.BigInteger(), nullable=False),
        sa.Column("section", sa.Text()),
        sa.Column("chunk_index", sa.Integer()),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM)),
        sa.Column("embed_model", sa.Text(), nullable=False),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', content)", persisted=True),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_chunks"),
        fk("article_id", "chunks", "articles", "CASCADE"),
    )
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index("ix_chunks_tsv", "chunks", ["tsv"], postgresql_using="gin")
    op.create_index("ix_chunks_article_id", "chunks", ["article_id"])

    op.create_table(
        "trials",
        sa.Column("nct_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text()),
        sa.Column("status", sa.Text()),
        sa.Column("phase", sa.Text()),
        sa.Column("enrollment", sa.Integer()),
        sa.Column("conditions", TEXT_ARRAY),
        sa.Column("interventions", TEXT_ARRAY),
        sa.Column("start_date", sa.Date()),
        sa.Column("completion_date", sa.Date()),
        timestamp_now("fetched_at"),
        sa.PrimaryKeyConstraint("nct_id", name="pk_trials"),
    )

    op.create_table(
        "saved_searches",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("pubmed_query", sa.Text(), nullable=False),
        sa.Column("cadence", sa.Text(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        timestamp_now("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_saved_searches"),
        fk("user_id", "saved_searches", "users", "CASCADE"),
        sa.CheckConstraint("cadence IN ('weekly', 'monthly')", name="ck_saved_searches_cadence"),
    )

    op.create_table(
        "delivered_articles",
        sa.Column("saved_search_id", sa.BigInteger(), nullable=False),
        sa.Column("article_id", sa.BigInteger(), nullable=False),
        timestamp_now("delivered_at"),
        sa.PrimaryKeyConstraint("saved_search_id", "article_id", name="pk_delivered_articles"),
        fk("saved_search_id", "delivered_articles", "saved_searches", "CASCADE"),
        fk("article_id", "delivered_articles", "articles", "CASCADE"),
    )

    op.create_table(
        "bookmarks",
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("article_id", sa.BigInteger(), nullable=False),
        timestamp_now("created_at"),
        sa.PrimaryKeyConstraint("user_id", "article_id", name="pk_bookmarks"),
        fk("user_id", "bookmarks", "users", "CASCADE"),
        fk("article_id", "bookmarks", "articles", "CASCADE"),
    )

    op.create_table(
        "query_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", UUID),
        sa.Column("mode", sa.Text()),
        sa.Column("user_query", sa.Text()),
        sa.Column("plan", postgresql.JSONB()),
        sa.Column("candidate_pmids", BIGINT_ARRAY),
        sa.Column("context_chunk_ids", BIGINT_ARRAY),
        sa.Column("answer", postgresql.JSONB()),
        sa.Column("evidence_gap", sa.Boolean()),
        sa.Column("verification", postgresql.JSONB()),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("cost_usd", MONEY),
        sa.Column("feedback", sa.SmallInteger()),
        sa.Column("trace_id", sa.Text()),
        timestamp_now("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_query_logs"),
        fk("user_id", "query_logs", "users", "SET NULL"),
    )

    op.create_table(
        "usage_daily",
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("cost_usd", MONEY, server_default=sa.text("0"), nullable=False),
        sa.Column("requests", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "day", name="pk_usage_daily"),
        fk("user_id", "usage_daily", "users", "CASCADE"),
    )


def downgrade() -> None:
    """Drop every table; the shared ``vector`` extension is left in place."""
    for table in reversed(TABLES_IN_DEPENDENCY_ORDER):
        op.drop_table(table)
