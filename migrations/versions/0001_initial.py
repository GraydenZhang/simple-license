"""V1.1 fresh-install schema; no legacy dorm-approval entities."""
from app.schema_migrations import upgrade_0001

revision='0001'
down_revision=None
branch_labels=None
depends_on=None

def upgrade():
    upgrade_0001()

def downgrade():
    raise RuntimeError('Destructive downgrade is disabled; restore a verified backup instead.')
