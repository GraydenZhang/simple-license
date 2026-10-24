"""Shared revision operations, also callable from the compiled CLI without .py scripts."""
from alembic import op
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, Column, String, MetaData, Table, select, insert, update, text
from .models import Base, engine, InvitationSequence

def upgrade_0001():
    bind = op.get_bind()
    Base.metadata.create_all(bind)
    if bind.dialect.name == 'sqlite':
        for operation in ('UPDATE', 'DELETE'):
            op.execute(f"CREATE TRIGGER audit_no_{operation.lower()} BEFORE {operation} ON audit_logs BEGIN SELECT RAISE(ABORT, 'Audit records are append-only'); END")
    elif bind.dialect.name == 'postgresql':
        op.execute("CREATE FUNCTION reject_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Audit records are append-only'; END; $$")
        op.execute('CREATE TRIGGER audit_immutable BEFORE UPDATE OR DELETE ON audit_logs FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()')

def upgrade_0002():
    if 'deleted_at' not in {c['name'] for c in inspect(op.get_bind()).get_columns('users')}:
        op.add_column('users', Column('deleted_at', String(40), nullable=True))

def upgrade_0003():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    bind = op.get_bind()
    if 'invitation_no' not in {c['name'] for c in inspect(bind).get_columns('invitations')}:
        op.add_column('invitations', Column('invitation_no', String(40), nullable=True))
    InvitationSequence.__table__.create(bind, checkfirst=True)
    rows = list(bind.execute(text('SELECT id, created_at, invitation_no FROM invitations ORDER BY created_at, id')).mappings())
    days = {}
    for row in rows:
        day = datetime.fromisoformat(row['created_at']).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d')
        days.setdefault(day, []).append(row)
    for day, group in days.items():
        # Preserve existing assigned numbers; backfill older rows in creation order.
        counter = bind.execute(select(InvitationSequence.last_number).where(InvitationSequence.day == day)).scalar_one_or_none()
        current = max([counter or 0] + [int(r['invitation_no'].rsplit('-',1)[1]) for r in group if r['invitation_no']])
        for row in group:
            if not row['invitation_no']:
                current += 1
                bind.execute(text('UPDATE invitations SET invitation_no=:number WHERE id=:id'),
                             dict(number=f'{day[2:]}-{current:03d}', id=row['id']))
        if counter is None:
            bind.execute(insert(InvitationSequence).values(day=day, last_number=current))
        else:
            bind.execute(update(InvitationSequence).where(InvitationSequence.day==day).values(last_number=current))
    names = {i['name'] for i in inspect(bind).get_indexes('invitations')}
    if 'uq_invitations_invitation_no' not in names:
        op.create_index('uq_invitations_invitation_no','invitations',['invitation_no'],unique=True)

def migrate():
    """Same linear revisions/version table as Alembic. Run with services stopped."""
    revisions = [('0001', upgrade_0001), ('0002', upgrade_0002), ('0003', upgrade_0003)]
    with engine.begin() as connection:
        versions = Table('alembic_version', MetaData(), Column('version_num', String(32), primary_key=True))
        versions.create(connection, checkfirst=True)
        rows = list(connection.execute(select(versions.c.version_num)).scalars())
        if len(rows) > 1 or (rows and rows[0] not in {r for r, _ in revisions}):
            raise RuntimeError('Unknown database revision; use a matching release or restore backup')
        current = rows[0] if rows else None
        start = next((i + 1 for i, (r, _) in enumerate(revisions) if r == current), 0)
        with Operations.context(MigrationContext.configure(connection)):
            for revision, upgrade in revisions[start:]:
                upgrade()
                connection.execute(update(versions).values(version_num=revision) if current else insert(versions).values(version_num=revision))
                current = revision
