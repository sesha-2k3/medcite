from __future__ import annotations

import asyncio

import numpy as np
import numpy.typing as npt
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import selectinload

from app.db.models import EMBEDDING_DIM, Article, Chunk, User
from app.db.session import create_session_factory
from tests.integration.helpers import fetch_public_tables

pytestmark = pytest.mark.integration

APP_TABLES = {
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
EXPECTED_INDEXES = {
    "ix_users_email_lower",
    "ix_chunks_embedding_hnsw",
    "ix_chunks_tsv",
    "ix_chunks_article_id",
}


def unit_vector(seed: int) -> npt.NDArray[np.float32]:
    vec = np.random.default_rng(seed).standard_normal(EMBEDDING_DIM).astype(np.float32)
    normalized: npt.NDArray[np.float32] = vec / np.linalg.norm(vec)
    return normalized


async def test_upgrade_creates_tables_indexes_and_extension(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        tables = set(
            (await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")))
            .scalars()
            .all()
        )
        indexes = set(
            (await conn.execute(text("SELECT indexname FROM pg_indexes WHERE schemaname='public'")))
            .scalars()
            .all()
        )
        extension = await conn.scalar(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        )
    assert APP_TABLES <= tables
    assert EXPECTED_INDEXES <= indexes
    assert extension is not None


def test_downgrade_base_removes_every_table_and_upgrade_restores_them(
    migrated: Config, database_url: str
) -> None:
    command.downgrade(migrated, "base")
    assert asyncio.run(fetch_public_tables(database_url)) == {"alembic_version"}

    command.upgrade(migrated, "head")
    assert APP_TABLES <= asyncio.run(fetch_public_tables(database_url))


async def test_article_with_chunks_round_trips(engine: AsyncEngine) -> None:
    sessions = create_session_factory(engine)
    results_vec, methods_vec = unit_vector(1), unit_vector(2)

    async with sessions() as session:
        article = Article(
            pmid=90000001,
            title="Dumbbell versus barbell curls for elbow flexor hypertrophy",
            pub_types=["Randomized Controlled Trial"],
            mesh_terms=["Resistance Training", "Muscle, Skeletal"],
            study_meta={"design": "rct", "sample_size": 24},
            chunks=[
                Chunk(
                    section="Abstract-RESULTS",
                    chunk_index=1,
                    content="Elbow flexor thickness increased similarly in both groups.",
                    embedding=results_vec,
                    embed_model="ncbi/MedCPT-Article-Encoder",
                ),
                Chunk(
                    section="Abstract-METHODS",
                    chunk_index=0,
                    content="Twenty-four trained men completed eight weeks of curls.",
                    embedding=methods_vec,
                    embed_model="ncbi/MedCPT-Article-Encoder",
                ),
            ],
        )
        session.add(article)
        await session.commit()
        article_id = article.id

    async with sessions() as session:
        loaded = await session.scalar(
            select(Article).where(Article.id == article_id).options(selectinload(Article.chunks))
        )
        assert loaded is not None
        assert loaded.is_retracted is False
        assert loaded.created_at is not None
        assert loaded.study_meta == {"design": "rct", "sample_size": 24}
        assert [c.chunk_index for c in loaded.chunks] == [0, 1]

        results_chunk = loaded.chunks[1]
        assert results_chunk.embedding is not None
        np.testing.assert_allclose(results_chunk.embedding, results_vec, rtol=1e-6)
        assert results_chunk.tsv is not None
        assert "elbow" in results_chunk.tsv

        nearest = await session.scalar(
            select(Chunk.id).order_by(Chunk.embedding.cosine_distance(results_vec)).limit(1)
        )
        assert nearest == results_chunk.id

        await session.execute(delete(Article).where(Article.id == article_id))
        await session.commit()
        remaining = await session.scalar(
            select(func.count()).select_from(Chunk).where(Chunk.article_id == article_id)
        )
        assert remaining == 0


async def test_users_identity_is_unique_and_ids_are_generated(engine: AsyncEngine) -> None:
    sessions = create_session_factory(engine)
    async with sessions() as session:
        user = User(auth_issuer="https://issuer.test", auth_subject="sub-1", email="a@b.test")
        session.add(user)
        await session.commit()
        assert user.id is not None

    async with sessions() as session:
        session.add(User(auth_issuer="https://issuer.test", auth_subject="sub-1"))
        with pytest.raises(IntegrityError):
            await session.commit()
