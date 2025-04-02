"""add if move is a pass

Revision ID: 1d50870a1181
Revises: 413237fcac5d
Create Date: 2025-04-02 11:28:54.922934

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1d50870a1181'
down_revision = '413237fcac5d'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column('moves', sa.Column('is_pass', sa.Boolean(), nullable=True, server_default='false'))

def downgrade() -> None:
    op.drop_column('moves', 'is_pass')
