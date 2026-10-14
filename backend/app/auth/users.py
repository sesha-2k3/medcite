"""Map a token's ``(iss, sub)`` to the internal user ID, creating the user on first request."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.tokens import Claims
from app.db.models import User as UserRow


@dataclass(frozen=True, slots=True)
class User:
    id: uuid.UUID
    email: str | None


class UserResolver(Protocol):
    async def resolve(self, claims: Claims) -> User: ...


class UserDirectory:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def resolve(self, claims: Claims) -> User:
        """One upsert: concurrent first requests from the same user can't create two rows."""
        new_row = insert(UserRow).values(
            auth_issuer=claims.issuer, auth_subject=claims.subject, email=claims.email
        )
        statement = new_row.on_conflict_do_update(
            index_elements=[UserRow.auth_issuer, UserRow.auth_subject],
            set_={"email": func.coalesce(new_row.excluded.email, UserRow.email)},
        ).returning(UserRow.id, UserRow.email)
        async with self._sessions.begin() as session:
            row = (await session.execute(statement)).one()
        return User(id=row.id, email=row.email)
