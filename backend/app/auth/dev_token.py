"""Development credentials (AUTH_MODE=dev only).

uv run python -m app.auth.dev_token --new-key     print a signing key for AUTH_DEV_SIGNING_KEY
uv run python -m app.auth.dev_token [--sub ID]    print a bearer token signed with it
"""

from __future__ import annotations

import argparse
import secrets
import sys

from app.auth.tokens import mint_dev_token
from app.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.auth.dev_token")
    parser.add_argument("--new-key", action="store_true", help="print a new signing key")
    parser.add_argument("--sub", default="dev-user", help="subject (user) of the token")
    parser.add_argument("--email", default="dev@example.com")
    parser.add_argument("--hours", type=int, default=12)
    args = parser.parse_args()
    if args.new_key:
        print(secrets.token_urlsafe(48))
        return

    settings = get_settings()
    if settings.auth_mode != "dev" or not settings.is_development:
        sys.exit("dev tokens are only accepted with AUTH_MODE=dev and APP_ENV=development")
    if settings.auth_dev_signing_key is None:
        sys.exit("set AUTH_DEV_SIGNING_KEY in backend/.env first (make dev-key)")
    print(
        mint_dev_token(
            settings.auth_dev_signing_key.get_secret_value(),
            subject=args.sub,
            audience=settings.auth_audience,
            email=args.email,
            ttl_s=args.hours * 3600,
        )
    )


if __name__ == "__main__":
    main()
