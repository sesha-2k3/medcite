from __future__ import annotations

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.db import models
from app.db.base import Base

EXPECTED_TABLES = {
    "users",
    "articles",
    "chunks",
    "trials",
    "saved_searches",
    "delivered_articles",
    "bookmarks",
    "query_logs",
    "usage_daily",
}
USER_OWNED_TABLES = {"saved_searches", "bookmarks", "query_logs", "usage_daily"}


def ddl(table_name: str) -> str:
    table = Base.metadata.tables[table_name]
    return str(CreateTable(table).compile(dialect=postgresql.dialect()))


def test_models_cover_the_full_schema() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


@pytest.mark.parametrize("table_name", sorted(USER_OWNED_TABLES))
def test_user_ids_reference_internal_users_only(table_name: str) -> None:
    column = Base.metadata.tables[table_name].c.user_id
    targets = {fk.target_fullname for fk in column.foreign_keys}
    assert targets == {"users.id"}


def test_chunks_tsv_is_generated_from_content() -> None:
    assert "GENERATED ALWAYS AS (to_tsvector('english', content)) STORED" in ddl("chunks")


def test_chunks_embedding_is_768_dim_vector() -> None:
    assert "embedding VECTOR(768)" in ddl("chunks")


def test_serial_primary_keys() -> None:
    assert "id BIGSERIAL NOT NULL" in ddl("articles")


def test_hnsw_index_uses_cosine_ops() -> None:
    indexes = {i.name: i for i in models.Chunk.__table__.indexes}
    index = indexes["ix_chunks_embedding_hnsw"]
    sql = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert "USING hnsw (embedding vector_cosine_ops)" in sql


def test_constraint_names_match_the_migration() -> None:
    users = ddl("users")
    assert "CONSTRAINT pk_users PRIMARY KEY" in users
    assert "CONSTRAINT uq_users_auth_issuer_auth_subject UNIQUE" in users
    assert "CONSTRAINT ck_saved_searches_cadence CHECK" in ddl("saved_searches")
    assert "CONSTRAINT fk_chunks_article_id_articles FOREIGN KEY" in ddl("chunks")
