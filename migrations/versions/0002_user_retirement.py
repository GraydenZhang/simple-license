"""Retire accounts while preserving approval and append-only audit references."""
from app.schema_migrations import upgrade_0002

revision='0002'
down_revision='0001'
branch_labels=None
depends_on=None

def upgrade():
    upgrade_0002()

def downgrade():
    raise RuntimeError('Restore a verified backup instead of destructive downgrade.')
