"""baseline existing supabase schema

Revision ID: 0001_baseline_existing_schema
Revises: 
Create Date: 2026-06-21 15:14:32.391526

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import geoalchemy2


# revision identifiers, used by Alembic.
revision: str = '0001_baseline_existing_schema'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    raise RuntimeError("The existing Supabase schema baseline cannot be downgraded safely.")
