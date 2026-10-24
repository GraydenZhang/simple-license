from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from app.main import approval_app
from app import main as views

def test_daily_numbers_rollover_and_parallel(staff, monkeypatch):
    class Clock(datetime):
        current=datetime(2026,10,24,15,59,tzinfo=timezone.utc)
        @classmethod
        def now(cls,tz=None):
            return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)
    monkeypatch.setattr(views,'datetime',Clock)
    def create(_):
        with TestClient(approval_app) as client:
            client.cookies.update(staff.cookies)
            response=client.post('/api/staff/invitations',json={},headers=dict(staff.headers))
            assert response.status_code==200,response.text
            return response.json()['invitation_no']
    with ThreadPoolExecutor(max_workers=4) as pool:
        numbers=list(pool.map(create,range(4)))
    assert set(numbers)=={'261024-001','261024-002','261024-003','261024-004'}
    Clock.current=datetime(2026,10,24,16,0,tzinfo=timezone.utc)
    assert create(None)=='261025-001'
    page=staff.get('/staff/invitations')
    assert '261024-001' in page.text and '261025-001' in page.text

def test_legacy_invitation_backfill_preserves_ids_tokens(monkeypatch):
    from app import schema_migrations
    engine=create_engine('sqlite:///:memory:')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)'))
        connection.execute(text("INSERT INTO alembic_version VALUES ('0002')"))
        connection.execute(text('CREATE TABLE invitations (id INTEGER PRIMARY KEY,created_at VARCHAR(40),token_hash VARCHAR(64))'))
        for ident,created in [(1,'2026-10-23T16:00:00+00:00'),(2,'2026-10-24T15:59:59+00:00'),(3,'2026-10-24T16:00:00+00:00')]:
            connection.execute(text('INSERT INTO invitations VALUES (:id,:created,:token)'),dict(id=ident,created=created,token=str(ident)*64))
    monkeypatch.setattr(schema_migrations,'engine',engine)
    schema_migrations.migrate()
    schema_migrations.migrate()
    with engine.connect() as connection:
        rows=connection.execute(text('SELECT id,invitation_no,token_hash FROM invitations ORDER BY id')).all()
        assert [(r.id,r.invitation_no) for r in rows]==[(1,'261024-001'),(2,'261024-002'),(3,'261025-001')]
        assert [r.token_hash for r in rows]==[str(i)*64 for i in range(1,4)]
