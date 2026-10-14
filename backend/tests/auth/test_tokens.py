from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError

from app.auth.tokens import (
    DEV_ISSUER,
    DevTokenVerifier,
    JwksKeys,
    JwksTokenVerifier,
    mint_dev_token,
)
from app.config import Settings
from app.errors import UnauthorizedError

DEV_KEY = "dev-signing-key-for-tests-only-0123456789abcdef"
AUDIENCE = "authenticated"
ISSUER = "https://auth.example.test/auth/v1"
JWKS_URL = "https://auth.example.test/.well-known/jwks.json"


async def test_dev_token_round_trip() -> None:
    token = mint_dev_token(DEV_KEY, subject="u1", audience=AUDIENCE, email="a@b.test")
    claims = await DevTokenVerifier(DEV_KEY, AUDIENCE).verify(token)
    assert (claims.issuer, claims.subject, claims.email) == (DEV_ISSUER, "u1", "a@b.test")


@pytest.mark.parametrize(
    "token",
    [
        mint_dev_token(DEV_KEY, subject="u1", audience=AUDIENCE, ttl_s=60, now=1_000_000),
        mint_dev_token(DEV_KEY, subject="u1", audience="someone-else"),
        mint_dev_token(DEV_KEY[::-1], subject="u1", audience=AUDIENCE),
        "not.a.token",
    ],
    ids=["expired", "wrong-audience", "wrong-key", "malformed"],
)
async def test_bad_dev_tokens_are_rejected(token: str) -> None:
    with pytest.raises(UnauthorizedError):
        await DevTokenVerifier(DEV_KEY, AUDIENCE).verify(token)


async def test_dev_verifier_without_a_key_fails_closed() -> None:
    token = mint_dev_token(DEV_KEY, subject="u1", audience=AUDIENCE)
    with pytest.raises(UnauthorizedError, match="AUTH_DEV_SIGNING_KEY"):
        await DevTokenVerifier(None, AUDIENCE).verify(token)


def test_dev_mode_is_refused_outside_development(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError, match="AUTH_MODE=dev"):
        make_settings(app_env="production", auth_mode="dev")


class Issuer:
    """A JWKS endpoint serving whichever keys are currently published."""

    def __init__(self) -> None:
        self.private: dict[str, rsa.RSAPrivateKey] = {}
        self.published: list[str] = []
        self.fetches = 0

    def add_key(self, kid: str) -> None:
        self.private[kid] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.published.append(kid)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.fetches += 1
        keys = []
        for kid in self.published:
            jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.private[kid].public_key()))
            keys.append({**jwk, "kid": kid, "alg": "RS256", "use": "sig"})
        return httpx.Response(200, json={"keys": keys})

    def token(self, kid: str, **claims: object) -> str:
        payload = {"iss": ISSUER, "sub": "user-1", "aud": AUDIENCE, "exp": 4_000_000_000}
        payload.update(claims)
        return jwt.encode(payload, self.private[kid], algorithm="RS256", headers={"kid": kid})


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def verifier_for(issuer: Issuer, clock: Clock) -> JwksTokenVerifier:
    http = httpx.AsyncClient(transport=httpx.MockTransport(issuer.handler))
    keys = JwksKeys(http, JWKS_URL, ttl_s=3600, clock=clock)
    return JwksTokenVerifier(keys, issuer=ISSUER, audience=AUDIENCE)


async def test_jwks_token_is_verified() -> None:
    issuer = Issuer()
    issuer.add_key("k1")
    claims = await verifier_for(issuer, Clock()).verify(issuer.token("k1", email="x@y.test"))
    assert (claims.issuer, claims.subject, claims.email) == (ISSUER, "user-1", "x@y.test")


@pytest.mark.parametrize(
    "claims",
    [{"iss": "https://evil.test"}, {"aud": "other"}, {"exp": 1}],
    ids=["issuer", "audience", "expired"],
)
async def test_jwks_claims_are_checked(claims: dict[str, object]) -> None:
    issuer = Issuer()
    issuer.add_key("k1")
    with pytest.raises(UnauthorizedError):
        await verifier_for(issuer, Clock()).verify(issuer.token("k1", **claims))


async def test_keys_are_cached() -> None:
    issuer, clock = Issuer(), Clock()
    issuer.add_key("k1")
    verifier = verifier_for(issuer, clock)
    for _ in range(3):
        await verifier.verify(issuer.token("k1"))
    assert issuer.fetches == 1


async def test_unknown_key_id_refetches_after_rotation() -> None:
    issuer, clock = Issuer(), Clock()
    issuer.add_key("k1")
    verifier = verifier_for(issuer, clock)
    await verifier.verify(issuer.token("k1"))
    issuer.add_key("k2")

    with pytest.raises(UnauthorizedError, match="unknown key"):
        await verifier.verify(issuer.token("k2"))  # too soon to refetch
    clock.now = 31.0
    await verifier.verify(issuer.token("k2"))
    assert issuer.fetches == 2


async def test_dev_tokens_are_rejected_in_jwks_mode() -> None:
    issuer = Issuer()
    issuer.add_key("k1")
    dev = mint_dev_token(DEV_KEY, subject="u1", audience=AUDIENCE)
    with pytest.raises(UnauthorizedError, match="accepted key"):
        await verifier_for(issuer, Clock()).verify(dev)


async def test_unreachable_jwks_fails_closed() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    issuer = Issuer()
    issuer.add_key("k1")
    http = httpx.AsyncClient(transport=httpx.MockTransport(down))
    verifier = JwksTokenVerifier(
        JwksKeys(http, JWKS_URL, ttl_s=3600), issuer=ISSUER, audience=AUDIENCE
    )
    with pytest.raises(UnauthorizedError, match="unavailable"):
        await verifier.verify(issuer.token("k1"))
