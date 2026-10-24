"""Isolated browser/HTTP QA server; never imports or changes the formal runtime DB."""
import os
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
QA=Path(os.getenv('QA_DATA_DIR',str(ROOT/'artifacts/browser-qa')))
os.environ.update(DATA_DIR=str(QA),DATABASE_URL='sqlite:///'+str(QA/'qa.db'),PUBLIC_BASE_URL='http://127.0.0.1:15101',VERIFY_BASE_URL='http://127.0.0.1:15102',APPROVAL_PORT='15101',VERIFY_PORT='15102',APP_ENV='test',COOKIE_SECURE='false')
# Real signatures in isolated QA; never a production bypass or shared signing key.
QA.mkdir(parents=True,exist_ok=True)
import base64,json
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from app import licensing
from app.license_protocol import canonical,fingerprint
private=Ed25519PrivateKey.generate()
public=base64.b64encode(private.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()
licensing.public_keys=lambda:{'http-qa':public}
machine=QA/('machine-id-'+sys.argv[1]);machine.write_text('0123456789abcdef0123456789abcdef')
license_file=QA/('license-'+sys.argv[1]+'.lic')
payload=dict(version=1,key_id='http-qa',product='school-permit',customer='隔离HTTP验收',fingerprint=fingerprint(machine.read_text()),issued_at='2020-01-01T00:00:00Z',expires_at='2099-01-01T00:00:00Z',license_id='http-qa')
license_file.write_text(json.dumps(dict(payload=payload,signature=base64.b64encode(private.sign(canonical(payload))).decode())))
os.environ.update(HOST_MACHINE_ID_FILE=str(machine),LICENSE_FILE=str(license_file))
if sys.argv[1]=='approval':
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import select
    from app.models import Session,User,Setting
    from app.main import passwords
    from app.config import ISSUER
    command.upgrade(Config('alembic.ini'),'head')
    with Session() as db:
        if not db.scalar(select(User).where(User.username=='qa_admin')):
            db.add(User(username='qa_admin',password_hash=passwords.hash('Isolated-QA-Secret-2026'),role='ADMIN',must_change_password=False))
        if not db.get(Setting,'issuer_name'): db.add(Setting(key='issuer_name',value=ISSUER))
        db.commit()
import uvicorn
uvicorn.run('app.main:'+sys.argv[1]+'_app',host='127.0.0.1',port=15101 if sys.argv[1]=='approval' else 15102,access_log=False,proxy_headers=False,server_header=False)
