from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.db.session import build_connect_args


def test_dev_auth_rejected_outside_development(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError, match="AUTH_MODE=dev"):
        make_settings(app_env="production", auth_mode="dev")


def test_jwks_mode_requires_url_and_issuer(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError, match="AUTH_JWKS_URL"):
        make_settings(app_env="staging", auth_mode="jwks")


def test_jwks_mode_accepted_when_configured(make_settings: Callable[..., Settings]) -> None:
    s = make_settings(
        app_env="production",
        auth_mode="jwks",
        auth_jwks_url="https://example.supabase.co/auth/v1/.well-known/jwks.json",
        auth_issuer="https://example.supabase.co/auth/v1",
    )
    assert s.auth_mode == "jwks"


def test_database_url_must_use_asyncpg(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError, match="asyncpg"):
        make_settings(database_url="postgresql://u:p@h/db")


def test_cors_origins_parsed_from_comma_list(make_settings: Callable[..., Settings]) -> None:
    s = make_settings(cors_origins="http://localhost:5173, https://app.example.com ,")
    assert s.cors_origin_list == ["http://localhost:5173", "https://app.example.com"]


def test_migrations_url_falls_back_to_app_url(make_settings: Callable[..., Settings]) -> None:
    s = make_settings()
    assert s.migrations_database_url == s.database_url


def test_statement_cache_disabled_behind_transaction_pooler(
    make_settings: Callable[..., Settings],
) -> None:
    args = build_connect_args(make_settings(db_statement_cache_size=0))
    assert args["statement_cache_size"] == 0
    assert args["prepared_statement_cache_size"] == 0
    names = {args["prepared_statement_name_func"]() for _ in range(3)}
    assert len(names) == 3  # unique names avoid collisions on shared server connections


def test_statement_cache_enabled_for_direct_connections(
    make_settings: Callable[..., Settings],
) -> None:
    args = build_connect_args(make_settings(db_statement_cache_size=100))
    assert args["statement_cache_size"] == 100
    assert "prepared_statement_name_func" not in args
