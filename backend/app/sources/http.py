"""Shared HTTP client construction for external sources."""

from __future__ import annotations

import httpx

from app.config import Settings


def create_http_client(settings: Settings) -> httpx.AsyncClient:
    """One pooled client per process; NCBI asks callers to identify themselves."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.ncbi_timeout_s, connect=5.0),
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        headers={"User-Agent": f"{settings.ncbi_tool}/0.1 (mailto:{settings.ncbi_email})"},
        follow_redirects=True,
    )
