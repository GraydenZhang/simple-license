"""All automated tests use isolated temporary DB/files; never touch the project database."""
import os
import tempfile
import pytest
from pathlib import Path
from datetime import datetime,timedelta,timezone

TEST_ROOT=Path(tempfile.mkdtemp(prefix='campus-permit-tests-'))
os.environ.update(DATA_DIR=str(TEST_ROOT),DATABASE_URL='sqlite:///'+str(TEST_ROOT/'test.db'),PUBLIC_BASE_URL='http://testserver',VERIFY_BASE_URL='http://verifyserver:15002',COOKIE_SECURE='false',APP_ENV='test')
from app.models import Base,engine,Session,User,Setting,StaffSession
from app.main import approval_app,verify_app,passwords
from app.config import ISSUER
from app.services import digest,token
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

@pytest.fixture
def licensed_runtime(tmp_path, monkeypatch):
    import base64, json
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from app import licensing
    from app.license_protocol import PRODUCT, fingerprint, canonical
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setattr(licensing, 'public_keys', lambda: {'qa-key': base64.b64encode(public).decode('ascii')})
    machine = tmp_path / 'machine-id'
    machine.write_text('0123456789abcdef0123456789abcdef', encoding='ascii')
    license_file = tmp_path / 'license.lic'
    monkeypatch.setenv('HOST_MACHINE_ID_FILE', str(machine))
    monkeypatch.setenv('LICENSE_FILE', str(license_file))
    payload = dict(version=1, key_id='qa-key', product=PRODUCT, customer='隔离测试单位',
                   fingerprint=fingerprint(machine.read_text()), issued_at='2020-01-01T00:00:00Z',
                   expires_at='2099-01-01T00:00:00Z', license_id='qa-license')
    def write(**changes):
        data = dict(payload, **changes)
        license_file.write_text(json.dumps({'payload': data, 'signature': base64.b64encode(private.sign(canonical(data))).decode('ascii')}), encoding='utf-8')
        return data
    write()
    return dict(write=write, path=license_file, machine=machine, private=private, payload=payload)

@pytest.fixture(autouse=True)
def fresh_db(licensed_runtime):
    Base.metadata.drop_all(engine)
    with engine.begin() as con:
        con.exec_driver_sql('DROP TABLE IF EXISTS alembic_version')
    command.upgrade(Config('alembic.ini'),'head')
    with Session() as db:
        db.add_all([User(username='qa_admin',password_hash=passwords.hash('Isolated-QA-Secret-2026'),role='ADMIN',must_change_password=False),User(username='qa_approver',password_hash=passwords.hash('Isolated-QA-Secret-2026'),role='APPROVER',must_change_password=False),Setting(key='issuer_name',value=ISSUER)])
        db.commit()

@pytest.fixture
def client():
    with TestClient(approval_app) as client: yield client

@pytest.fixture
def verify():
    with TestClient(verify_app) as client: yield client

def authenticate(client,username='qa_admin'):
    assert client.post('/api/staff/session',json={'username':username,'password':'Isolated-QA-Secret-2026'}).status_code==200
    from sqlalchemy import select
    with Session() as db:
        s=db.get(StaffSession,digest(client.cookies.get('staff_session')))
        client.headers.update({'X-CSRF-Token':s.csrf})
    return client

@pytest.fixture
def staff(client): return authenticate(client)

@pytest.fixture
def approver(client): return authenticate(client,'qa_approver')

@pytest.fixture
def sample():
    return {'unit_name':'验收测试单位','reason':'宿舍网络线路维护','leader_name':'测试负责人','leader_phone':'0000-00000000','location_type':'DORMITORY','dormitories':[1,3,11],'start_date':'2026-10-24','end_date':'2026-10-26','worker_count':None,'remark':'仅用于隔离自动验收'}

def invite(client):
    res=client.post('/api/staff/invitations',json={'label':'隔离验收'})
    assert res.status_code==200,res.text
    return res.json()['url'].split('/')[-1]

def submit(client,raw,data):
    return client.post('/api/apply/'+raw+'/submit',json=data,headers={'X-Requested-With':'campus-permits'})

def issue(client,raw,data):
    from app.models import Application
    from sqlalchemy import select
    assert submit(client,raw,data).status_code==200
    with Session() as db: id=db.scalar(select(Application).where(Application.invitation_id!=None)).id
    res=client.post(f'/api/staff/applications/{id}/approve',json={})
    assert res.status_code==200,res.text
    return res.json()['id']

def verify_token(client,id):
    import io
    import zxingcpp
    from PIL import Image
    img=client.get(f'/api/staff/permits/{id}/image')
    assert img.status_code==200
    decoded=zxingcpp.read_barcode(Image.open(io.BytesIO(img.content)))
    assert decoded and decoded.text.startswith('http://verifyserver:15002/verify/')
    return decoded.text.split('/')[-1]
