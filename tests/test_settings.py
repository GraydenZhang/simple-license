import io
import json
from pathlib import Path
from datetime import datetime,timedelta,timezone
import pytest
import zxingcpp
from PIL import Image
from sqlalchemy import select,func
from app import config as c
from app.models import Session,Setting,User,StaffSession,Invitation,Application,AuditLog,VerificationLog,now
from app.main import passwords
from .conftest import invite,issue,submit,verify_token

def decode(staff,pid):
    image=Image.open(io.BytesIO(staff.get(f'/api/staff/permits/{pid}/image').content))
    assert image.size==(1080,1920)
    result=zxingcpp.read_barcode(image);assert result
    return result.text

def test_external_prefixes_persist_and_leave_internal_navigation_unchanged(staff,sample):
    old=issue(staff,invite(staff),sample)
    old_image=staff.get(f'/api/staff/permits/{old}/image').content
    original=(c.PUBLIC_URL,c.VERIFY_URL)
    result=staff.put('/api/staff/settings/access',json={'public_base_url':'https://apply.example.com/school/','verify_base_url':'https://verify.example.com:8443/campus/','verify_display_prefix':'verify.example.com'})
    assert result.status_code==200
    created=staff.post('/api/staff/invitations',json={}).json()
    assert created['url'].startswith('https://apply.example.com/school/apply/')
    raw=created['url'].rsplit('/',1)[1]
    assert submit(staff,raw,sample).status_code==200
    with Session() as db:
        inv=db.scalar(select(Invitation).where(Invitation.id==created['id']))
        aid=db.scalar(select(Application.id).where(Application.invitation_id==inv.id))
    pid=staff.post(f'/api/staff/applications/{aid}/approve',json={}).json()['id']
    assert decode(staff,pid).startswith('https://verify.example.com:8443/campus/verify/')
    assert staff.get('/staff/settings').status_code==200
    assert staff.get('/staff/dashboard').status_code==200
    assert (c.PUBLIC_URL,c.VERIFY_URL)==original
    assert staff.get(f'/api/staff/permits/{old}/image').content==old_image

@pytest.mark.parametrize('value',['javascript:alert(1)','ftp://example.com','http://user:secret@example.com','http://example.com?q=x','http://example.com#fragment','http://example.com:99999','http://','http://exa mple.com','http://example.com/../escape',None])
def test_invalid_output_prefix_rejected_atomically(staff,value):
    result=staff.put('/api/staff/settings/access',json={'public_base_url':'https://valid.example.com','verify_base_url':value})
    assert result.status_code==422
    with Session() as db: assert not db.get(Setting,'public_base_url')

def test_new_settings_are_admin_only(approver):
    for url,body in [('/api/staff/settings/access',{'public_base_url':'https://a.example.com','verify_base_url':'https://v.example.com'}),('/api/staff/settings/branding',{'header_name':'Unapproved'})]:
        assert approver.put(url,json=body).status_code==403
    assert approver.post('/api/staff/settings/branding/logo',files={'file':('x.png',b'bad','image/png')}).status_code==403

def test_unit_name_links_to_verify_footer_but_not_signer(staff,verify,sample):
    assert staff.put('/api/staff/settings/branding',json={'header_name':'仅管理右上角测试单位'}).status_code==200
    img=io.BytesIO();Image.new('RGB',(100,100),'green').save(img,'PNG')
    assert staff.post('/api/staff/settings/branding/logo',files={'file':('logo.png',img.getvalue(),'image/png')}).status_code==200
    assert staff.get('/api/staff/branding/logo').content==img.getvalue()
    page=staff.get('/staff/dashboard').text
    assert '仅管理右上角测试单位' in page and '校园施工许可' in page
    assert '<section class="welcome-banner"' not in page
    raw=invite(staff)
    assert '仅管理右上角测试单位' not in staff.get('/apply/'+raw).text
    pid=issue(staff,raw,sample);token=verify_token(staff,pid)
    assert '<footer>仅管理右上角测试单位 · 校内使用' in verify.get('/verify/'+token).text
    assert staff.put('/api/staff/settings/branding',json={'header_name':'更新后的单位名称'}).status_code==200
    assert '<footer>更新后的单位名称 · 校内使用' in verify.get('/verify/'+token).text
    assert verify.get('/api/staff/branding/logo').status_code==404
    with Session() as db:
        from app.models import Permit
        assert json.loads(db.get(Permit,pid).snapshot_json)['issuer_name']==c.ISSUER
        assert not db.get(Setting,'issuer_logo')

def test_pending_dot_tracks_actual_pending_count(staff,sample):
    assert 'class="nav-dot"' not in staff.get('/staff/dashboard').text
    raw=invite(staff);assert submit(staff,raw,sample).status_code==200
    assert 'class="nav-dot"' in staff.get('/staff/dashboard').text
    with Session() as db: aid=db.scalar(select(Application.id))
    assert staff.post(f'/api/staff/applications/{aid}/approve',json={}).status_code==200
    page=staff.get('/staff/dashboard').text
    assert 'class="nav-dot"' not in page
    assert 'class="user-menu"' in page and '修改密码' in page and '退出登录' in page
    assert '<a class="user-chip" href="/staff/password"' not in page

def test_clock_uses_local_asset_and_does_not_create_more_visits(staff,verify,sample):
    token=verify_token(staff,issue(staff,invite(staff),sample))
    result=verify.get('/verify/'+token)
    assert 'data-server-now=' in result.text and '当前时间' in result.text
    assert 'script-src \'self\'' in result.headers['content-security-policy']
    assert verify.get('/verify/assets/clock.js').status_code==200
    assert verify.get('/verify/assets/clock.js').status_code==200
    with Session() as db: assert db.scalar(select(func.count()).select_from(VerificationLog))==1
    script=(c.ROOT/'app/verify_static/clock.js').read_text('utf-8')
    assert 'setInterval(tick,1000)' in script and 'fetch(' not in script

def test_default_retention_is_360(staff):
    assert 'value="360"' in staff.get('/staff/settings').text
    assert staff.put('/api/staff/settings/issuer',json={'issuer_name':c.ISSUER}).status_code==200
    with Session() as db: assert db.get(Setting,'visit_log_retention_days').value=='360'

def test_retention_job_keeps_logs_between_180_and_360_days(staff,verify,sample):
    import subprocess,sys,os
    token=verify_token(staff,issue(staff,invite(staff),sample))
    for _ in range(3):verify.get('/verify/'+token)
    with Session() as db:
        logs=db.scalars(select(VerificationLog).order_by(VerificationLog.id)).all()
        logs[0].visited_at=(datetime.now(timezone.utc)-timedelta(days=361)).isoformat()
        logs[1].visited_at=(datetime.now(timezone.utc)-timedelta(days=181)).isoformat()
        db.commit()
    result=subprocess.run([sys.executable,'-m','scripts.maintenance'],env=dict(os.environ),capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    with Session() as db:assert db.scalar(select(func.count()).select_from(VerificationLog))==2

@pytest.mark.parametrize('base,expected',[
    ('http://192.0.2.10:15002','192.0.2.10'),
    ('https://verify.example.com:8443/campus','verify.example.com'),
    ('http://school.example.com','school.example.com'),
    ('https://127.0.0.1:443/nested/path','127.0.0.1'),
])
def test_notice_automatically_uses_host_without_protocol_or_port(staff,sample,base,expected):
    from app.models import Permit
    with Session() as db:db.add(Setting(key='verify_display_prefix',value='obsolete-manual-hint'));db.commit()
    assert staff.put('/api/staff/settings/access',json={'public_base_url':'http://testserver','verify_base_url':base,'verify_display_prefix':'ignored-client-hint'}).status_code==200
    id=issue(staff,invite(staff),sample)
    with Session() as db:
        assert json.loads(db.get(Permit,id).snapshot_json)['verify_display_prefix']==expected
    assert decode(staff,id).startswith(base+'/verify/')
    assert f'<output id="verify-host-preview">{expected}</output>' in staff.get('/staff/settings').text
    assert 'name="verify_display_prefix"' not in staff.get('/staff/settings').text

def test_header_logo_restore_default_preserves_name_and_old_permit(staff,sample):
    id=issue(staff,invite(staff),sample);before=staff.get(f'/api/staff/permits/{id}/image').content
    assert staff.put('/api/staff/settings/branding',json={'header_name':'保留的单位名称'}).status_code==200
    picture=io.BytesIO();Image.new('RGB',(80,80),'green').save(picture,'PNG')
    assert staff.post('/api/staff/settings/branding/logo',files={'file':('logo.png',picture.getvalue(),'image/png')}).status_code==200
    assert staff.get('/api/staff/branding/logo').status_code==200
    assert staff.delete('/api/staff/settings/branding/logo').status_code==200
    assert staff.get('/api/staff/branding/logo').status_code==404
    assert staff.delete('/api/staff/settings/branding/logo').status_code==200
    assert '保留的单位名称' in staff.get('/staff/dashboard').text
    assert '恢复默认' in staff.get('/staff/settings').text
    assert staff.get(f'/api/staff/permits/{id}/image').content==before

def test_only_admin_can_restore_header_logo(approver):
    assert approver.delete('/api/staff/settings/branding/logo').status_code==403
