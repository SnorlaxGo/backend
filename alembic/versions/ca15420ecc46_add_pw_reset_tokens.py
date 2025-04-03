"""add pw reset tokens

Revision ID: ca15420ecc46
Revises: 1d50870a1181
Create Date: 2025-04-03 11:43:36.802747

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ca15420ecc46'
down_revision = '1d50870a1181'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('reset_token', sa.String(), nullable=True))
    op.add_column('users', sa.Column('reset_token_expires', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'reset_token')
    op.drop_column('users', 'reset_token_expires')
