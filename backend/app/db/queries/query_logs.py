"""Query logs (one row per answered request) and per-user daily usage."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import QueryLog, UsageDaily


@dataclass(frozen=True, slots=True)
class CompletedQuery:
    user_id: uuid.UUID
    mode: str | None
    user_query: str
    plan: dict[str, Any] | None
    candidate_pmids: list[int]
    context_chunk_ids: list[int]
    answer: dict[str, Any] | None
    evidence_gap: bool | None
    verification: dict[str, Any] | None
    latency_ms: int
    cost_usd: Decimal
    trace_id: str


class QueryLogRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def record(self, entry: CompletedQuery) -> int:
        row = QueryLog(
            user_id=entry.user_id,
            mode=entry.mode,
            user_query=entry.user_query,
            plan=entry.plan,
            candidate_pmids=entry.candidate_pmids,
            context_chunk_ids=entry.context_chunk_ids,
            answer=entry.answer,
            evidence_gap=entry.evidence_gap,
            verification=entry.verification,
            latency_ms=entry.latency_ms,
            cost_usd=entry.cost_usd,
            trace_id=entry.trace_id,
        )
        async with self._sessions.begin() as session:
            session.add(row)
            await session.flush()
            return row.id

    async def set_feedback(self, query_id: int, user_id: uuid.UUID, value: int) -> bool:
        """Only the user who asked can rate an answer; False when no such query is theirs."""
        statement = (
            update(QueryLog)
            .where(QueryLog.id == query_id, QueryLog.user_id == user_id)
            .values(feedback=value)
            .returning(QueryLog.id)
        )
        async with self._sessions.begin() as session:
            return (await session.execute(statement)).first() is not None


class UsageRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def cost_on(self, user_id: uuid.UUID, day: date) -> Decimal:
        statement = select(UsageDaily.cost_usd).where(
            UsageDaily.user_id == user_id, UsageDaily.day == day
        )
        async with self._sessions() as session:
            cost = (await session.execute(statement)).scalar_one_or_none()
        return cost if cost is not None else Decimal(0)

    async def add(self, user_id: uuid.UUID, day: date, cost_usd: Decimal) -> None:
        """Count one request and its cost; one upsert, safe under concurrent requests."""
        new_row = insert(UsageDaily).values(user_id=user_id, day=day, cost_usd=cost_usd, requests=1)
        statement = new_row.on_conflict_do_update(
            index_elements=[UsageDaily.user_id, UsageDaily.day],
            set_={
                "cost_usd": UsageDaily.cost_usd + new_row.excluded.cost_usd,
                "requests": UsageDaily.requests + 1,
            },
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)
