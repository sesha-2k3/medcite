"""The FastAPI dependency every ``/v1`` route uses to identify its caller."""

from __future__ import annotations

from typing import Annotated

from fastapi import Header, Request

from app.auth.tokens import TokenVerifier
from app.auth.users import User, UserResolver
from app.errors import UnauthorizedError


async def current_user(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> User:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise UnauthorizedError("missing bearer token")
    verifier: TokenVerifier = request.app.state.verifier
    users: UserResolver = request.app.state.users
    claims = await verifier.verify(token.strip())
    return await users.resolve(claims)
