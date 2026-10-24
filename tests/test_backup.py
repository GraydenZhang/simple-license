import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from sqlalchemy import select
from fastapi.testclient import TestClient
from app import config as c
from app.main import approval_app,verify_app,get_db
from app.models import Session,Permit,AuditLog,VerificationLog,Attachment,engine
from .conftest import invite,submit,issue,verify_token

def test_AT26_backup_restore_and_restart(staff,verify,sample,tmp_path,monkeypatch):
    raw=invite(staff)
    image=__import__('io').BytesIO()
    from PIL import Image
    Image.new('RGB',(50,50),'white').save(image,'PNG')
    result=staff.post(f'/api/apply/{raw}/attachments',data={'kind':'WORKER_PHOTO'},files={'file':('person.png',image.getvalue(),'image/png')},headers={'X-Requested-With':'campus-permits'})
    assert result.status_code==200
    attachment_id=result.json()['id']
    id=issue(staff,raw,sample); verification=verify_token(staff,id)
    verify.get('/verify/'+verification)
    before=staff.get(f'/api/staff/permits/{id}/image').content
    backup=tmp_path/'backup'; env=dict(os.environ)
    result=subprocess.run([sys.executable,'-m','scripts.backup',str(backup)],cwd=c.ROOT,env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    with sqlite3.connect(backup/'campus.db') as database:
        assert database.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        for name in ['applications','application_revisions','permits','verification_access_logs','audit_logs','attachments']:
            assert database.execute('SELECT count(*) FROM '+name).fetchone()[0]>0
    # Open the restored set with a new DB engine and independent service clients.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    restored_engine=create_engine('sqlite:///'+str(backup/'campus.db'),connect_args={'check_same_thread':False})
    RestoredSession=sessionmaker(restored_engine,expire_on_commit=False)
    def restored_db():
        with RestoredSession() as db: yield db
    monkeypatch.setattr(c,'UPLOADS',backup/'uploads'); monkeypatch.setattr(c,'IMAGES',backup/'images')
    approval_app.dependency_overrides[get_db]=restored_db; verify_app.dependency_overrides[get_db]=restored_db
    try:
        with TestClient(approval_app) as restarted,TestClient(verify_app) as restarted_verify:
            restarted.cookies.update(staff.cookies)
            assert restarted.get(f'/api/staff/permits/{id}/image').content==before
            assert restarted.get('/api/staff/attachments/'+str(attachment_id)).status_code==200
            assert restarted_verify.get('/verify/'+verification).status_code==200
            assert restarted.get('/staff/audit').status_code==200
    finally:
        approval_app.dependency_overrides.clear(); verify_app.dependency_overrides.clear(); restored_engine.dispose()
