"""Add articles.publication_status.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-14

PubMed's PublicationStatus (for example "aheadofprint" or "ppublish") drives the
ahead-of-print evidence modifier. Additive and nullable, so it is safe to deploy before the
code that writes it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("publication_status", sa.Text()))


def downgrade() -> None:
    op.drop_column("articles", "publication_status")
