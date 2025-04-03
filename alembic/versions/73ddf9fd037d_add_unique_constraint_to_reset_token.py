"""Add unique constraint to reset_token

Revision ID: 73ddf9fd037d
Revises: ca15420ecc46
Create Date: 2025-04-03 16:19:27.315197

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '73ddf9fd037d'
down_revision = 'ca15420ecc46'
branch_labels = None
depends_on = None


def upgrade():
    # First, ensure all existing reset_tokens are NULL or unique
    op.execute("UPDATE users SET reset_token = NULL WHERE reset_token IS NOT NULL")
    
    # Then add the unique constraint and index
    op.create_index('idx_reset_token', 'users', ['reset_token'])
    op.create_unique_constraint('uq_users_reset_token', 'users', ['reset_token'])

def downgrade():
    # Remove the constraint and index
    op.drop_constraint('uq_users_reset_token', 'users', type_='unique')
    op.drop_index('idx_reset_token', 'users')