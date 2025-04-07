"""Add auth provider

Revision ID: c64c09a887d4
Revises: 73ddf9fd037d
Create Date: 2025-04-07 11:46:26.796281

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from datetime import datetime


# revision identifiers, used by Alembic.
revision = 'c64c09a887d4'
down_revision = '73ddf9fd037d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create auth_providers table
    op.create_table(
        'auth_providers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('provider', sa.String(), nullable=False),
        sa.Column('provider_user_id', sa.String(), nullable=False),
        sa.Column('provider_email', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True, default=datetime.utcnow),
        sa.Column('updated_at', sa.DateTime(), nullable=True, default=datetime.utcnow, onupdate=datetime.utcnow),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider', 'provider_user_id', name='uix_provider_id')
    )
    op.create_index(op.f('ix_auth_providers_id'), 'auth_providers', ['id'], unique=False)
    
    # Modify users table to make hashed_password nullable
    op.alter_column('users', 'hashed_password', 
                    existing_type=sa.String(), 
                    nullable=True)


def downgrade() -> None:
    # Drop auth_providers table
    op.drop_index(op.f('ix_auth_providers_id'), table_name='auth_providers')
    op.drop_table('auth_providers')
    
    # Make hashed_password non-nullable again
    op.alter_column('users', 'hashed_password', 
                    existing_type=sa.String(), 
                    nullable=False)
