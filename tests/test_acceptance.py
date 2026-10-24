import io
import json
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import pytest
from sqlalchemy import select,func,text
from sqlalchemy.exc import IntegrityError
from PIL import Image
from pypdf import PdfWriter
from app import config as c
from app.models import *
from app.services import status,digest
from .conftest import invite,submit,issue,verify_token,authenticate

def pdf_bytes():
    out=io.BytesIO(); writer=PdfWriter(); writer.add_blank_page(width=100,height=100); writer.write(out); return out.getvalue()

def test_AT01_initial_accounts_idempotent(client):
    from scripts.init_db import initialize
    import secrets
    credentials={name:secrets.token_urlsafe(18) for name in ('admin','approver')}
    assert set(initialize(credentials))=={'admin','approver'}
    for name,secret in credentials.items():
        result=client.post('/api/staff/session',json={'username':name,'password':secret})
        assert result.status_code==200 and result.json()['redirect']=='/staff/dashboard'
        assert result.json()['recommend_password_change'] is True
        assert client.get('/staff/dashboard').status_code==200
        assert client.post('/api/staff/session',json={'username':name,'password':secret}).json()['recommend_password_change'] is False
    with Session() as db:
        old_hash=db.scalar(select(User.password_hash).where(User.username=='approver'))
    assert initialize(credentials)==[]
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(User).where(User.username.in_(credentials)))==2
        assert db.scalar(select(User.password_hash).where(User.username=='approver'))==old_hash
        assert old_hash.startswith('$argon2id$') and all(p not in old_hash for p in credentials.values())


def test_AT02_03_route_isolation_and_roles(client,verify):
    for path in ['/','/staff/login','/staff/dashboard','/apply/fake','/api/staff/session','/api/staff/permits/1/image','/docs','/openapi.json','/api/verify/fake/confirm']:
        assert verify.get(path).status_code==404,path
    assert client.get('/').status_code==404
    assert client.post('/api/staff/applications/1/approve',json={}).status_code==401
    authenticate(client,'qa_approver')
    assert client.get('/staff/users').status_code==403
    assert client.put('/api/staff/settings/issuer',json={'issuer_name':'变更'}).status_code==403
    assert client.get('/api/staff/verification-logs/export').status_code==403
    with Session() as db:
        assert set(db.scalars(select(User.role)).all())=={'ADMIN','APPROVER'}

def test_AT04_05_invite_expiry_and_result(staff,sample):
    raw=invite(staff)
    with Session() as db:
        inv=db.scalar(select(Invitation)); inv.expires_at=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(); db.commit()
    assert submit(staff,raw,sample).status_code==410
    raw=invite(staff)
    assert submit(staff,raw,sample).status_code==200
    with Session() as db:
        inv=db.scalar(select(Invitation).where(Invitation.token_hash==digest(raw))); inv.expires_at=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat(); db.commit()
    assert staff.get('/apply/'+raw).status_code==200
    with Session() as db:
        inv=db.scalar(select(Invitation).where(Invitation.token_hash==digest(raw)))
        assert inv.token_hash!=raw

@pytest.mark.parametrize('change',[{'unit_name':''},{'reason':''},{'leader_name':''},{'leader_phone':'123'},{'dormitories':[]},{'dormitories':[12]},{'location_type':'OTHER','other_location_text':''},{'end_date':'2026-10-20'},{'start_date':'2026-02-30'},{'worker_count':0},{'worker_count':10000}])
def test_AT06_07_08_validation(staff,sample,change):
    raw=invite(staff); assert submit(staff,raw,dict(sample,**change)).status_code==422

def test_AT07_all_dorms_other_and_AT09_optional(staff,sample):
    for changes in [{'dormitories':list(range(1,12))},{'location_type':'OTHER','other_location_text':'图书馆五楼','end_date':sample['start_date']}]:
        raw=invite(staff); assert submit(staff,raw,dict(sample,**changes)).status_code==200
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Attachment))==0

def test_AT10_pdf_limits_and_file_validation(staff):
    raw=invite(staff); endpoint=f'/api/apply/{raw}/attachments'; headers={'X-Requested-With':'campus-permits'}
    assert staff.post(endpoint,data={'kind':'APPROVAL_LETTER'},files={'file':('bad.pdf',b'fake','application/pdf')},headers=headers).status_code==422
    assert staff.post(endpoint,data={'kind':'WORKER_PHOTO'},files={'file':('bad.png',b'fake','image/png')},headers=headers).status_code==422
    for n in range(5):
        result=staff.post(endpoint,data={'kind':'APPROVAL_LETTER' if n%2 else 'CONSTRUCTION_PLAN'},files={'file':('test.pdf',pdf_bytes(),'application/pdf')},headers=headers)
        assert result.status_code==200,result.text
    assert staff.post(endpoint,data={'kind':'APPROVAL_LETTER'},files={'file':('six.pdf',pdf_bytes(),'application/pdf')},headers=headers).status_code==422
    image=io.BytesIO(); Image.new('RGB',(40,40),'white').save(image,'PNG')
    assert staff.post(endpoint,data={'kind':'WORKER_PHOTO'},files={'file':('test.png',image.getvalue(),'image/png')},headers=headers).status_code==200

def test_AT11_reject_resubmit_history_and_private_files(staff,sample):
    raw=invite(staff); headers={'X-Requested-With':'campus-permits'}
    attachment=staff.post(f'/api/apply/{raw}/attachments',data={'kind':'APPROVAL_LETTER'},files={'file':('test.pdf',pdf_bytes(),'application/pdf')},headers=headers).json()['id']
    assert submit(staff,raw,sample).status_code==200
    with Session() as db: id=db.scalar(select(Application.id))
    assert staff.post(f'/api/staff/applications/{id}/reject',json={'reason':''}).status_code==422
    assert staff.post(f'/api/staff/applications/{id}/reject',json={'reason':'请补充施工事由'}).status_code==200
    assert staff.delete(f'/api/apply/{raw}/attachments/{attachment}',headers=headers).status_code==200
    assert submit(staff,raw,dict(sample,reason='修改后的事由')).status_code==200
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Application))==1
        assert db.scalar(select(func.count()).select_from(Revision))==2
        assert db.get(Attachment,attachment).is_current is False
        assert json.loads(db.scalar(select(Revision).order_by(Revision.id)).snapshot_json)['reason']==sample['reason']
    assert staff.get(f'/api/staff/attachments/{attachment}').status_code==200

def test_AT12_13_png_qr_and_idempotency(staff,sample):
    raw=invite(staff); id=issue(staff,raw,sample)
    content=staff.get(f'/api/staff/permits/{id}/image').content
    assert Image.open(io.BytesIO(content)).size==(1080,1920)
    raw_token=verify_token(staff,id)
    import zxingcpp
    for width in (375,390):
        phone=Image.open(io.BytesIO(content)).resize((width,width*16//9),Image.Resampling.LANCZOS)
        decoded=zxingcpp.read_barcode(phone)
        assert decoded and decoded.text=='http://verifyserver:15002/verify/'+raw_token
    with Session() as db:
        p=db.get(Permit,id); application_id=p.application_id; snapshot=json.loads(p.snapshot_json)
        import re
        assert re.fullmatch(r'PERMIT-\d{8}-[0-9A-F]{8}',p.permit_no)
        assert p.permit_no in staff.get(f'/staff/permits/{id}').text
    again=staff.post(f'/api/staff/applications/{application_id}/approve',json={})
    assert again.json()['id']==id
    with Session() as db: assert db.scalar(select(func.count()).select_from(Permit))==1
    assert snapshot['issuer_name']==c.ISSUER and snapshot['daily_start']=='08:00' and snapshot['daily_end']=='18:30'
    assert staff.get('/api/apply/'+raw+'/permit-image').content==content

def test_AT14_issuer_snapshot(staff,sample):
    id=issue(staff,invite(staff),sample); old=staff.get(f'/api/staff/permits/{id}/image').content
    assert staff.put('/api/staff/settings/issuer',json={'issuer_name':'新的签发单位','retention':180}).status_code==200
    raw=invite(staff); submit(staff,raw,sample)
    with Session() as db: aid=db.scalar(select(Application.id).order_by(Application.id.desc()))
    new_id=staff.post(f'/api/staff/applications/{aid}/approve',json={}).json()['id']
    with Session() as db:
        assert json.loads(db.get(Permit,id).snapshot_json)['issuer_name']==c.ISSUER
        assert json.loads(db.get(Permit,new_id).snapshot_json)['issuer_name']=='新的签发单位'
    assert staff.get(f'/api/staff/permits/{id}/image').content==old

def test_AT15_16_17_anonymous_minimal_logs_cookies(staff,verify,sample):
    id=issue(staff,invite(staff),sample); raw=verify_token(staff,id)
    verify.headers.update({'User-Agent':'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1'})
    for _ in range(2):
        result=verify.get('/verify/'+raw); assert result.status_code==200
        assert sample['leader_phone'] not in result.text
        assert '/staff/login' not in result.text and '<button' not in result.text and '<form' not in result.text
    verify.get('/verify/assets/verify.css')
    with Session() as db:
        logs=db.scalars(select(VerificationLog)).all(); assert len(logs)==2
        assert logs[0].anon_device_id==logs[1].anon_device_id
        assert logs[0].device_type=='手机'
        first=logs[0].anon_device_id
    verify.cookies.clear(); assert verify.get('/verify/'+raw).status_code==200
    with Session() as db: assert db.scalar(select(VerificationLog).order_by(VerificationLog.id.desc())).anon_device_id!=first
    assert verify.get('/verify/unknown').status_code==404
    with Session() as db: assert db.scalar(select(VerificationLog).order_by(VerificationLog.id.desc())).permit_id is None

def test_AT18_proxy_headers(staff,verify,sample,monkeypatch):
    raw=verify_token(staff,issue(staff,invite(staff),sample))
    verify.get('/verify/'+raw,headers={'X-Forwarded-For':'203.0.113.8'})
    with Session() as db: assert db.scalar(select(VerificationLog).order_by(VerificationLog.id.desc())).source_ip=='testclient'
    from app.services import ip
    monkeypatch.setattr(c,'TRUSTED_PROXIES',['10.0.0.0/8'])
    request=SimpleNamespace(client=SimpleNamespace(host='10.1.1.1'),headers={'x-forwarded-for':'198.51.100.66, 203.0.113.8, 10.2.2.2'})
    assert ip(request)=='203.0.113.8'

@pytest.mark.parametrize('at,expected',[('2026-10-25T10:00:00+08:00','NOT_STARTED'),('2026-10-26T07:59:59+08:00','OUTSIDE_HOURS'),('2026-10-26T08:00:00+08:00','VALID'),('2026-10-26T18:30:00+08:00','OUTSIDE_HOURS'),('2026-10-27T09:00:00+08:00','VALID'),('2026-10-28T18:29:59+08:00','VALID'),('2026-10-28T18:30:00+08:00','EXPIRED'),('2026-10-29T08:00:00+08:00','EXPIRED')])
def test_AT19_20_time_boundaries(at,expected):
    p=SimpleNamespace(start_date='2026-10-26',end_date='2026-10-28',revoked_at=None)
    assert status(p,datetime.fromisoformat(at))==expected

def test_AT21_22_revoke_reissue(staff,verify,sample):
    id=issue(staff,invite(staff),sample); old_raw=verify_token(staff,id)
    assert staff.post(f'/api/staff/permits/{id}/reissue',json={}).status_code==409
    assert staff.post(f'/api/staff/permits/{id}/revoke',json={'reason':'更正施工日期'}).status_code==200
    assert 'id="verification-state">已撤销</h1>' in verify.get('/verify/'+old_raw).text
    result=staff.post(f'/api/staff/permits/{id}/reissue',json={'data':dict(sample,end_date='2026-10-27')}); assert result.status_code==200
    new_id=result.json()['id']; new_raw=verify_token(staff,new_id)
    assert new_id!=id and new_raw!=old_raw
    assert 'id="verification-state">已撤销</h1>' in verify.get('/verify/'+old_raw).text
    assert 'id="verification-state">已撤销</h1>' not in verify.get('/verify/'+new_raw).text
    with Session() as db: assert db.scalar(select(func.count()).select_from(Permit).where(Permit.active_application_id!=None))==1

def test_AT23_private_resource_isolation(verify):
    for path in ['/uploads/test.pdf','/images/test.png','/api/staff/attachments/1','/assets/app.js','/api/apply/fake/permit-image']:
        assert verify.get(path).status_code==404
    assert verify.post('/verify/fake').status_code==405

def test_AT24_missing_config_and_snapshot(staff,sample,monkeypatch):
    monkeypatch.setattr(c,'PUBLIC_URL',''); assert staff.post('/api/staff/invitations',json={}).status_code==503
    monkeypatch.setattr(c,'PUBLIC_URL','http://changed:15001'); raw=invite(staff)
    assert submit(staff,raw,sample).status_code==200
    with Session() as db: aid=db.scalar(select(Application.id))
    monkeypatch.setattr(c,'VERIFY_URL',''); assert staff.post(f'/api/staff/applications/{aid}/approve',json={}).status_code==503
    monkeypatch.setattr(c,'VERIFY_URL','http://verifyserver:15002'); id=staff.post(f'/api/staff/applications/{aid}/approve',json={}).json()['id']
    before=staff.get(f'/api/staff/permits/{id}/image').content
    monkeypatch.setattr(c,'VERIFY_URL','http://changed:15002')
    assert staff.get(f'/api/staff/permits/{id}/image').content==before

def test_AT25_disable_invite_does_not_revoke(staff,verify,sample):
    raw=invite(staff); id=issue(staff,raw,sample); verification=verify_token(staff,id)
    with Session() as db: inv=db.scalar(select(Invitation).where(Invitation.token_hash==digest(raw)))
    assert staff.post(f'/api/staff/invitations/{inv.id}/disable',json={}).status_code==200
    assert staff.get('/apply/'+raw).status_code==410
    assert staff.get('/api/apply/'+raw+'/permit-image').status_code==410
    assert verify.get('/verify/'+verification).status_code==200
    with Session() as db: assert db.get(Permit,id).revoked_at is None

def test_staff_pages_render_and_log_filters_export(staff,verify,sample):
    id=issue(staff,invite(staff),sample); raw=verify_token(staff,id); verify.get('/verify/'+raw)
    paths=['dashboard','invitations','applications','applications/1','permits',f'permits/{id}','users','settings','audit','audit?tab=actions','password']
    for path in paths:
        res=staff.get('/staff/'+path); assert res.status_code==200,(path,res.text[:200])
    logs=staff.get(f'/api/staff/verification-logs?permit_id={id}').json(); assert len(logs)==1
    assert staff.get('/api/staff/verification-logs?device_type=手机').json()==[]
    csv=staff.get(f'/api/staff/verification-logs/export?permit_id={id}'); assert csv.status_code==200 and 'anon_device_id' in csv.text
    with Session() as db:
        with pytest.raises(Exception):
            db.execute(text("UPDATE audit_logs SET action='edited'")); db.commit()

def test_csrf_and_optional_password_change(client):
    authenticate(client)
    assert client.post('/api/staff/invitations',json={},headers={'X-CSRF-Token':'wrong'}).status_code==403
    assert client.post('/api/staff/invitations',json={},headers={'Origin':'http://attacker'}).status_code==403
    assert client.post('/api/apply/fake/submit',json={}).status_code==403
    with Session() as db:
        row=db.scalar(select(User).where(User.username=='qa_admin')); row.must_change_password=True; db.commit()
    assert client.post('/api/staff/invitations',json={}).status_code==200
    result=client.post('/api/staff/password',json={'old_password':'Isolated-QA-Secret-2026','new_password':'Changed-Test-Secret-2026'}); assert result.status_code==200
    assert client.get('/staff/dashboard').status_code==200

def test_upload_ownership(staff):
    raw=invite(staff); other=invite(staff); headers={'X-Requested-With':'campus-permits'}
    result=staff.post(f'/api/apply/{raw}/attachments',data={'kind':'APPROVAL_LETTER'},files={'file':('test.pdf',pdf_bytes(),'application/pdf')},headers=headers)
    id=result.json()['id']
    assert staff.delete(f'/api/apply/{other}/attachments/{id}',headers=headers).status_code==404

def test_rate_limit(client):
    for _ in range(10): assert client.post('/api/staff/session',json={'username':'missing','password':'wrong'}).status_code==401
    assert client.post('/api/staff/session',json={'username':'missing','password':'wrong'}).status_code==429

def test_signer_logo_feature_removed_and_audit_export_preserved(staff,sample):
    picture=io.BytesIO();Image.new('RGB',(150,100),'#367a62').save(picture,'PNG')
    assert staff.post('/api/staff/settings/logo',files={'file':('logo.png',picture.getvalue(),'image/png')}).status_code==404
    assert staff.get('/api/staff/settings/logo').status_code==404
    with Session() as db:db.add(Setting(key='issuer_logo',value='legacy-logo'));db.commit()
    id=issue(staff,invite(staff),sample)
    with Session() as db:assert 'issuer_logo' not in json.loads(db.get(Permit,id).snapshot_json)
    assert '凭证签发单位标志' not in staff.get('/staff/settings').text
    assert staff.get('/api/staff/audit-logs/export?action=创建邀请').status_code==200
    assert staff.get('/staff/audit?tab=actions&action=创建邀请').status_code==200


def test_output_prefix_is_independent_of_listener_port(staff,sample,monkeypatch):
    raw=invite(staff); assert submit(staff,raw,sample).status_code==200
    with Session() as db: aid=db.scalar(select(Application.id))
    monkeypatch.setattr(c,'VERIFY_URL','http://127.0.0.1:15001')
    assert staff.post(f'/api/staff/applications/{aid}/approve',json={}).status_code==200

def test_malformed_json_is_client_error(staff):
    assert staff.post('/api/staff/invitations',content='broken',headers={'content-type':'application/json'}).status_code==400
    assert staff.post('/api/staff/invitations',json=[]).status_code==422
    assert staff.post('/api/staff/session',content='{}',headers={'content-type':'text/plain'}).status_code==415

def test_longest_certificate_fields_fit(staff,sample):
    # Maximum approved field lengths exercise dynamic portrait text fitting.
    assert staff.put('/api/staff/settings/issuer',json={'issuer_name':'测'*100,'retention':180}).status_code==200
    extended=dict(sample,unit_name='单'*100,reason='事'*150,leader_name='人'*50,leader_phone='1'*30,location_type='OTHER',other_location_text='地'*500)
    id=issue(staff,invite(staff),extended)
    assert verify_token(staff,id)

def test_scheduled_retention_preserves_audit(staff,verify,sample):
    import subprocess,sys,os
    id=issue(staff,invite(staff),sample); raw=verify_token(staff,id)
    verify.get('/verify/'+raw); verify.get('/verify/'+raw)
    with Session() as db:
        old=db.scalar(select(VerificationLog).order_by(VerificationLog.id)); old.visited_at=(datetime.now(timezone.utc)-timedelta(days=361)).isoformat()
        audits=db.scalar(select(func.count()).select_from(AuditLog)); db.commit()
    result=subprocess.run([sys.executable,'-m','scripts.maintenance'],env=dict(os.environ),capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(VerificationLog))==1
        assert db.scalar(select(func.count()).select_from(AuditLog))==audits+1
        assert db.scalar(select(AuditLog).order_by(AuditLog.id.desc())).action=='按保留期限清理访问日志'

def test_invitation_status_uses_precise_utc_expiry(staff):
    future=invite(staff); expired=invite(staff)
    with Session() as db:
        a=db.scalar(select(Invitation).where(Invitation.token_hash==digest(future)))
        b=db.scalar(select(Invitation).where(Invitation.token_hash==digest(expired)))
        a.label='future-boundary'; b.label='past-boundary'
        a.expires_at=(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat()
        b.expires_at=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
        db.commit()
    import re
    rows=re.findall(r'<tr>(.*?)</tr>',staff.get('/staff/invitations').text,re.S)
    future_row=next(row for row in rows if 'future-boundary' in row)
    expired_row=next(row for row in rows if 'past-boundary' in row)
    assert '待填写' in future_row
    assert '已过期' in expired_row

def test_approver_create_reset_disable_and_logout(staff):
    password='Temporary-Approver-Secret-2026'
    result=staff.post('/api/staff/users',json={'username':'new_approver','password':password})
    assert result.status_code==200
    with Session() as db:
        user=db.scalar(select(User).where(User.username=='new_approver')); id=user.id
        assert user.role=='APPROVER' and user.must_change_password
    assert staff.post('/api/staff/users',json={'username':'new_approver','password':password}).status_code==409
    from fastapi.testclient import TestClient
    from app.main import approval_app
    with TestClient(approval_app) as new_client:
        first=new_client.post('/api/staff/session',json={'username':'new_approver','password':password}).json()
        assert first['redirect']=='/staff/dashboard' and first['recommend_password_change'] is True
        assert staff.post(f'/api/staff/users/{id}',json={'password':'Reset-Approver-Secret-2026'}).status_code==200
        assert new_client.get('/staff/password',follow_redirects=False).status_code==303
        assert staff.post(f'/api/staff/users/{id}',json={'is_active':False}).status_code==200
        assert new_client.post('/api/staff/session',json={'username':'new_approver','password':'Reset-Approver-Secret-2026'}).status_code==401
    assert staff.post('/api/staff/logout',json={}).status_code==200
    assert staff.get('/staff/dashboard',follow_redirects=False).status_code==303

def test_update_user_rejects_malformed_body_without_mutation(staff):
    with Session() as db:
        uid=db.scalar(select(User.id).where(User.username=='qa_approver'))
    url=f'/api/staff/users/{uid}'
    assert staff.post(url,content='broken',headers={'content-type':'application/json'}).status_code==400
    assert staff.post(url,json=['password']).status_code==422
    assert staff.post(url,content='{}',headers={'content-type':'text/plain'}).status_code==415
    with Session() as db:
        assert db.get(User,uid).is_active

@pytest.mark.parametrize('change',[
    {'unit_name':None}, {'reason':[]}, {'leader_name':{}},
    {'leader_phone':None}, {'location_type':'OTHER','other_location_text':None},
    {'dormitories':[1.5]}, {'dormitories':[True]}, {'dormitories':'11'},
    {'remark':{'unexpected':'object'}},
])
def test_application_rejects_invalid_field_types(staff,sample,change):
    raw=invite(staff)
    assert submit(staff,raw,dict(sample,**change)).status_code==422
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Application))==0
