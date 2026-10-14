"""JWT verification (portability rule 4): nothing outside ``app/auth`` knows the provider.

``jwks`` mode verifies tokens from any standard issuer (Supabase Auth when deployed) against
its published keys. ``dev`` mode verifies HS256 tokens signed with ``AUTH_DEV_SIGNING_KEY``,
for offline development; the settings refuse ``dev`` outside ``APP_ENV=development``.
Every failure raises ``UnauthorizedError``: verification fails closed.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import jwt

from app.config import Settings
from app.errors import UnauthorizedError

logger = logging.getLogger(__name__)

DEV_ISSUER = "medcite-dev"
DEV_ALGORITHM = "HS256"
JWKS_ALGORITHMS = frozenset({"RS256", "ES256"})
REQUIRED_CLAIMS = ["exp", "iss", "sub", "aud"]
# An unknown key ID refetches the key set at most this often, so forged tokens can't make
# us hammer the issuer.
MIN_REFRESH_INTERVAL_S = 30.0


@dataclass(frozen=True, slots=True)
class Claims:
    issuer: str
    subject: str
    email: str | None


class TokenVerifier(Protocol):
    async def verify(self, token: str) -> Claims: ...


def _claims(payload: dict[str, Any]) -> Claims:
    email = payload.get("email")
    return Claims(
        issuer=str(payload["iss"]),
        subject=str(payload["sub"]),
        email=email if isinstance(email, str) else None,
    )


class DevTokenVerifier:
    def __init__(self, signing_key: str | None, audience: str) -> None:
        self._key = signing_key
        self._audience = audience

    async def verify(self, token: str) -> Claims:
        if not self._key:
            raise UnauthorizedError("AUTH_DEV_SIGNING_KEY is not set; run make dev-key")
        try:
            payload = jwt.decode(
                token,
                self._key,
                algorithms=[DEV_ALGORITHM],
                audience=self._audience,
                issuer=DEV_ISSUER,
                options={"require": REQUIRED_CLAIMS},
            )
        except jwt.PyJWTError as exc:
            raise UnauthorizedError(f"invalid token: {exc}") from exc
        return _claims(payload)


class JwksKeys:
    """The issuer's signing keys, cached for ``ttl_s`` and refetched on an unknown key ID."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        url: str,
        *,
        ttl_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._url = url
        self._ttl_s = ttl_s
        self._clock = clock
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float | None = None

    async def get(self, kid: str) -> jwt.PyJWK:
        now = self._clock()
        stale = self._fetched_at is None or now - self._fetched_at >= self._ttl_s
        unknown = kid not in self._keys
        may_refresh = self._fetched_at is None or now - self._fetched_at >= MIN_REFRESH_INTERVAL_S
        if stale or (unknown and may_refresh):
            await self._refresh(now)
        key = self._keys.get(kid)
        if key is None:
            raise UnauthorizedError("token signed with an unknown key")
        return key

    async def _refresh(self, now: float) -> None:
        try:
            response = await self._http.get(self._url)
            response.raise_for_status()
            key_set = jwt.PyJWKSet.from_dict(response.json())
        except (httpx.HTTPError, ValueError, jwt.PyJWTError) as exc:
            logger.warning("jwks_fetch_failed", extra={"error": str(exc)})
            if not self._keys:
                raise UnauthorizedError("signing keys are unavailable") from exc
            return
        self._keys = {key.key_id: key for key in key_set.keys if key.key_id}
        self._fetched_at = now


class JwksTokenVerifier:
    def __init__(self, keys: JwksKeys, *, issuer: str, audience: str) -> None:
        self._keys = keys
        self._issuer = issuer
        self._audience = audience

    async def verify(self, token: str) -> Claims:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise UnauthorizedError(f"invalid token: {exc}") from exc
        algorithm, kid = header.get("alg"), header.get("kid")
        if algorithm not in JWKS_ALGORITHMS or not isinstance(kid, str):
            raise UnauthorizedError("token is not signed with an accepted key")
        key = await self._keys.get(kid)
        try:
            payload = jwt.decode(
                token,
                key.key,
                algorithms=[algorithm],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": REQUIRED_CLAIMS},
            )
        except jwt.PyJWTError as exc:
            raise UnauthorizedError(f"invalid token: {exc}") from exc
        return _claims(payload)


def mint_dev_token(
    signing_key: str,
    *,
    subject: str,
    audience: str,
    email: str | None = None,
    ttl_s: int = 12 * 3600,
    now: float | None = None,
) -> str:
    issued = int(now if now is not None else time.time())
    payload: dict[str, Any] = {
        "iss": DEV_ISSUER,
        "sub": subject,
        "aud": audience,
        "iat": issued,
        "exp": issued + ttl_s,
    }
    if email:
        payload["email"] = email
    return jwt.encode(payload, signing_key, algorithm=DEV_ALGORITHM)


def build_verifier(settings: Settings, http: httpx.AsyncClient) -> TokenVerifier:
    if settings.auth_mode == "dev":
        key = settings.auth_dev_signing_key
        return DevTokenVerifier(key.get_secret_value() if key else None, settings.auth_audience)
    if settings.auth_jwks_url is None or settings.auth_issuer is None:
        raise ValueError("AUTH_MODE=jwks requires AUTH_JWKS_URL and AUTH_ISSUER")
    keys = JwksKeys(http, settings.auth_jwks_url, ttl_s=settings.auth_jwks_cache_ttl_s)
    return JwksTokenVerifier(keys, issuer=settings.auth_issuer, audience=settings.auth_audience)
