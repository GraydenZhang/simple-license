"""Stable daily invitation numbers; retains internal IDs and existing token links."""
from app.schema_migrations import upgrade_0003

revision='0003'
down_revision='0002'
branch_labels=None
depends_on=None

def upgrade():
    upgrade_0003()

def downgrade():
    raise RuntimeError('Restore a verified backup instead of destructive downgrade.')
