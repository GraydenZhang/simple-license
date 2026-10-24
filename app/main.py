import csv
import io
import json
import secrets
import uuid
import hashlib
from datetime import datetime, timedelta, timezone, date, time
from pathlib import Path
from urllib.parse import urlparse
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, delete, text
from sqlalchemy.exc import IntegrityError, OperationalError
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from PIL import Image
from pypdf import PdfReader
from user_agents import parse as parse_ua
from . import config as c
from .models import *
from .services import *
from .licensing import require_license, license_lifespan, check_license

passwords=PasswordHasher()
templates=Jinja2Templates(directory=str(c.ROOT/'app/templates'))
templates.env.filters['localtime']=local
templates.env.filters['unjson']=json.loads
templates.env.globals.update(status_label=STATUSES,permit_status=status,today=lambda:datetime.now(c.TZ).date().isoformat())

async def json_body(request):
    if request.headers.get('content-type','').split(';')[0].strip()!='application/json':
        raise HTTPException(415,'请使用JSON格式提交数据')
    try: body=await request.json()
    except (ValueError,UnicodeError): raise HTTPException(400,'JSON数据格式无效')
    if not isinstance(body,dict): raise HTTPException(422,'请求内容必须是一个JSON对象')
    return body

def get_db(request:Request):
    # Default deny for business writes, including future API write routes.
    # Account security remains available so data owners can authenticate/export.
    security_paths = {'/api/staff/session', '/api/staff/logout', '/api/staff/password'}
    if request.method not in ('GET','HEAD','OPTIONS') and request.url.path not in security_paths:
        require_license()
    with Session() as db:
        try:
            if request.method not in ('GET','HEAD','OPTIONS') and request.url.path not in security_paths:
                from sqlalchemy import event
                event.listen(db, 'before_commit', lambda session: require_license())
            # Serialize SQLite writers; PostgreSQL uses row locks and unique constraints.
            if DB_URL.startswith('sqlite') and request.method not in ('GET','HEAD','OPTIONS'):
                db.execute(text('BEGIN IMMEDIATE'))
            yield db
        except Exception:
            db.rollback(); raise

def staff(request:Request, db=Depends(get_db)):
    sess=db.get(StaffSession,digest(request.cookies.get('staff_session','')))
    user=db.get(User,sess.user_id) if sess and sess.expires_at>now() else None
    if not user or not user.is_active or user.deleted_at: raise HTTPException(401,'请先登录')
    if request.method not in ('GET','HEAD','OPTIONS') and not secrets.compare_digest(request.headers.get('x-csrf-token',''),sess.csrf):
        raise HTTPException(403,'请求校验失败，请刷新页面后重试')
    request.state.user=user; request.state.csrf=sess.csrf
    return user

def admin(user=Depends(staff)):
    if user.role!='ADMIN': raise HTTPException(403,'仅系统管理员可以执行此操作')
    return user

def setting(db,key,default=''):
    row=db.get(Setting,key); return row.value if row else default

def save_setting(db,key,value):
    row=db.get(Setting,key)
    if row: row.value=value
    else: db.add(Setting(key=key,value=value))

def output_base(db,kind):
    return setting(db,kind+'_base_url',c.PUBLIC_URL if kind=='public' else c.VERIFY_URL)

def validate_base(value):
    if not isinstance(value,str) or len(value)>500: raise HTTPException(422,'网址前缀格式无效')
    value=value.strip().rstrip('/')
    try:
        parsed=urlparse(value)
        port=parsed.port
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or any(x.isspace() for x in value) or '\\' in value:
            raise ValueError()
        if port is not None and not 1<=port<=65535: raise ValueError()
        if any(part in ('.','..') for part in parsed.path.split('/')): raise ValueError()
    except ValueError: raise HTTPException(422,'请输入完整http或https网址前缀，不含账号、查询参数或片段')
    return value

def page(request,name,db,user=None,**context):
    pending=db.scalar(select(func.count()).select_from(Application).where(Application.status=='PENDING')) if user else 0
    return templates.TemplateResponse(request=request,name=name,context=dict(user=user,csrf=getattr(request.state,'csrf',''),issuer=setting(db,'issuer_name',c.ISSUER),header_name=setting(db,'header_name','示例单位'),header_logo=bool(setting(db,'header_logo')),pending_count=pending,path=request.url.path,**context))

def find(db,model,id):
    row=db.get(model,id)
    if not row: raise HTTPException(404,'记录不存在')
    return row

def invitation(db,raw):
    inv=db.scalar(select(Invitation).where(Invitation.token_hash==digest(raw)).with_for_update()) if len(raw)<=100 else None
    if not inv or inv.disabled_at: raise HTTPException(410,'申请链接不存在或已被停用，请联系管理人员')
    deadline=inv.access_ends_at if inv.first_submitted_at else inv.expires_at
    if deadline and deadline<=now(): raise HTTPException(410,'申请链接已超过访问期限，请联系管理人员')
    return inv

def editable(db,inv):
    app=db.scalar(select(Application).where(Application.invitation_id==inv.id).with_for_update())
    if app and app.status not in ('REJECTED',): raise HTTPException(409,'申请已提交审批，当前不能编辑')
    return app

def attachment_rows(db,inv):
    return db.scalars(select(Attachment).where(Attachment.invitation_id==inv.id,Attachment.is_current==True).order_by(Attachment.id)).all()

def issue(db,request,user,application,data,reissued_from=None):
    require_license()
    verify_base=output_base(db,'verify')
    if not verify_base: raise HTTPException(503,'请先配置核验网址前缀，再签发许可证')
    try: verify_base=validate_base(verify_base)
    except HTTPException: raise HTTPException(503,'核验网址前缀配置无效')
    active=db.scalar(select(Permit).where(Permit.active_application_id==application.id))
    if active: return active
    raw=token(); issued=now()
    number='PERMIT-'+datetime.now(c.TZ).strftime('%Y%m%d')+'-'+secrets.token_hex(4).upper()
    snapshot=dict(data,issuer_name=setting(db,'issuer_name',c.ISSUER),issued_at=issued,daily_start='08:00',daily_end='18:30',timezone='Asia/Shanghai',image_width=1080,image_height=1920,verify_display_prefix=urlparse(verify_base).hostname)
    key=uuid.uuid4().hex+'.png'
    make_image(snapshot,number,verify_base+'/verify/'+raw,key)
    permit=Permit(application_id=application.id,active_application_id=application.id,permit_no=number,verify_token_hash=digest(raw),snapshot_json=dumps(snapshot),start_date=data['start_date'],end_date=data['end_date'],issued_at=issued,issued_by=user.id,image_storage_key=key,reissued_from_id=reissued_from)
    db.add(permit)
    application.status='APPROVED'; application.reviewed_by=user.id; application.reviewed_at=issued
    inv=db.get(Invitation,application.invitation_id)
    inv.access_ends_at=(datetime.combine(date.fromisoformat(data['end_date']),time(18,30),c.TZ)+timedelta(days=c.limit('INVITE_RESULT_AFTER_PERMIT_DAYS',30))).astimezone(timezone.utc).isoformat()
    try:
        db.flush()
        audit(db,request,user,'重新签发' if reissued_from else '通过并签发',permit.id,{'application_id':application.id,'original':json.loads(application.data_json),'approved':data})
        db.commit()
    except Exception:
        db.rollback(); (c.IMAGES/key).unlink(missing_ok=True); raise
    return permit

def create_approval_app():
    app=FastAPI(title='校园电子施工许可证 · 审批服务',docs_url=None,redoc_url=None,openapi_url=None,lifespan=license_lifespan)
    app.mount('/assets',StaticFiles(directory=c.ROOT/'app/static'),name='assets')

    @app.get('/api/staff/license')
    def license_status(user=Depends(admin)):
        from dataclasses import asdict
        return asdict(check_license())

    @app.middleware('http')
    async def security(request,call_next):
        if request.method not in ('GET','HEAD','OPTIONS'):
            origin=request.headers.get('origin')
            allowed={str(request.base_url).rstrip('/'),c.PUBLIC_URL}
            if origin and origin not in allowed: return JSONResponse({'detail':'不允许跨站请求'},403)
            if request.url.path.startswith('/api/apply/') and request.headers.get('x-requested-with')!='campus-permits':
                return JSONResponse({'detail':'请求校验失败'},403)
        response=await call_next(request)
        response.headers.update({'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Content-Security-Policy':"default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    @app.exception_handler(HTTPException)
    async def errors(request,exc):
        if request.url.path.startswith('/api/'): return JSONResponse({'detail':exc.detail},exc.status_code)
        if exc.status_code==401: return RedirectResponse('/staff/login',303)
        return templates.TemplateResponse(request=request,name='error.html',context={'code':exc.status_code,'message':exc.detail},status_code=exc.status_code)

    @app.exception_handler(IntegrityError)
    async def conflicts(request,exc): return JSONResponse({'detail':'数据已更新，请刷新后重试，未重复创建记录'},409)

    @app.get('/staff/login',response_class=HTMLResponse)
    def login_page(request:Request,db=Depends(get_db)):
        return page(request,'login.html',db)

    @app.post('/api/staff/session')
    async def login(request:Request,db=Depends(get_db)):
        body=await json_body(request); source=ip(request)
        threshold=(datetime.now(timezone.utc)-timedelta(minutes=15)).isoformat()
        count=db.scalar(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.source_ip==source,LoginAttempt.created_at>=threshold))
        if count>=10: raise HTTPException(429,'登录尝试过多，请15分钟后重试')
        user=db.scalar(select(User).where(User.username==str(body.get('username',''))[:80]))
        valid=False
        try:
            # Constant-work dummy hash for unknown usernames.
            valid=passwords.verify(user.password_hash if user else passwords.hash(secrets.token_hex(16)),str(body.get('password','')))
        except (VerificationError,InvalidHashError): pass
        if not user or not user.is_active or user.deleted_at or not valid:
            db.add(LoginAttempt(source_ip=source)); db.commit(); raise HTTPException(401,'用户名或密码错误，或账号已停用')
        recommend_password_change=bool(user.must_change_password)
        user.must_change_password=False
        raw=token(); csrf=token()
        db.add(StaffSession(token_hash=digest(raw),user_id=user.id,csrf=csrf,expires_at=(datetime.now(timezone.utc)+timedelta(hours=8)).isoformat()))
        audit(db,request,user,'登录'); db.commit()
        response=JSONResponse({'redirect':'/staff/dashboard','recommend_password_change':recommend_password_change})
        response.set_cookie('staff_session',raw,httponly=True,samesite='strict',secure=c.SECURE,max_age=8*3600,path='/')
        return response

    @app.post('/api/staff/logout')
    def logout(request:Request,user=Depends(staff),db=Depends(get_db)):
        db.execute(delete(StaffSession).where(StaffSession.token_hash==digest(request.cookies.get('staff_session','')))); db.commit()
        res=JSONResponse({'redirect':'/staff/login'}); res.delete_cookie('staff_session'); return res

    @app.get('/staff/password')
    def password_page(request:Request,user=Depends(staff),db=Depends(get_db)):
        return page(request,'password.html',db,user=user)

    @app.post('/api/staff/password')
    async def change_password(request:Request,user=Depends(staff),db=Depends(get_db)):
        body=await json_body(request); new=str(body.get('new_password',''))
        try: passwords.verify(user.password_hash,body.get('old_password',''))
        except (VerificationError,InvalidHashError): raise HTTPException(400,'原密码不正确')
        if not 12<=len(new)<=128 or new==body.get('old_password'): raise HTTPException(422,'新密码须为12—128位，且不能与原密码相同')
        user.password_hash=passwords.hash(new); user.must_change_password=False
        current=digest(request.cookies.get('staff_session',''))
        db.execute(delete(StaffSession).where(StaffSession.user_id==user.id,StaffSession.token_hash!=current))
        audit(db,request,user,'修改密码'); db.commit(); return {'redirect':'/staff/dashboard'}

    @app.get('/staff/dashboard')
    def dashboard(request:Request,user=Depends(staff),db=Depends(get_db)):
        applications=db.scalars(select(Application).order_by(Application.id.desc())).all()
        permits=db.scalars(select(Permit)).all()
        visits=db.scalar(select(func.count()).select_from(VerificationLog))
        logs=db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(5)).all()
        users={u.id:u.username for u in db.scalars(select(User)).all()}
        return page(request,'dashboard.html',db,user=user,applications=applications,permits=permits,visits=visits,logs=logs,names=users,stats={'pending':sum(a.status=='PENDING' for a in applications),'issued':len(permits),'valid':sum(status(p)=='VALID' for p in permits),'revoked':sum(bool(p.revoked_at) for p in permits)})

    @app.get('/staff/invitations')
    def invites_page(request:Request,user=Depends(staff),db=Depends(get_db)):
        invs=db.scalars(select(Invitation).order_by(Invitation.id.desc())).all()
        apps={a.invitation_id:a for a in db.scalars(select(Application)).all()}
        return page(request,'invitations.html',db,user=user,invitations=invs,applications=apps,public_configured=bool(output_base(db,'public')),current_utc=now())

    @app.post('/api/staff/invitations')
    async def create_invite(request:Request,user=Depends(staff),db=Depends(get_db)):
        public_base=output_base(db,'public')
        if not public_base: raise HTTPException(503,'请先配置申请网址前缀，才能生成申请链接')
        body=await json_body(request); raw=token()
        created=datetime.now(timezone.utc)
        day=created.astimezone(c.TZ).strftime('%Y%m%d')
        if db.bind.dialect.name=='postgresql':
            from sqlalchemy.dialects.postgresql import insert as sequence_insert
        else:
            from sqlalchemy.dialects.sqlite import insert as sequence_insert
        statement=sequence_insert(InvitationSequence).values(day=day,last_number=1)
        sequence=db.execute(statement.on_conflict_do_update(index_elements=['day'],set_={'last_number':InvitationSequence.last_number+1}).returning(InvitationSequence.last_number)).scalar_one()
        invitation_no=f'{day[2:]}-{sequence:03d}'
        inv=Invitation(invitation_no=invitation_no,token_hash=digest(raw),created_by=user.id,label=str(body.get('label','')).strip()[:100],created_at=created.isoformat(),expires_at=(created+timedelta(days=c.limit('INVITE_FIRST_SUBMIT_DAYS',7))).isoformat())
        db.add(inv); db.flush(); audit(db,request,user,'创建邀请',inv.id); db.commit()
        url=public_base+'/apply/'+raw
        return {'id':inv.id,'invitation_no':inv.invitation_no,'url':url,'qr':qr_data(url),'expires_at':local(inv.expires_at)}

    @app.post('/api/staff/invitations/{id}/disable')
    def disable_invite(id:int,request:Request,user=Depends(staff),db=Depends(get_db)):
        inv=find(db,Invitation,id); inv.disabled_at=now(); audit(db,request,user,'停用邀请',id); db.commit(); return {'ok':True}

    @app.get('/apply/{raw}')
    def apply_page(raw:str,request:Request,db=Depends(get_db)):
        inv=invitation(db,raw); application=db.scalar(select(Application).where(Application.invitation_id==inv.id))
        permit=db.scalar(select(Permit).where(Permit.application_id==application.id).order_by(Permit.id.desc())) if application else None
        return page(request,'apply.html',db,invite=inv,application=application,data=json.loads(application.data_json) if application else {},attachments=attachment_rows(db,inv),permit=permit,raw=raw)

    @app.post('/api/apply/{raw}/submit')
    async def submit(raw:str,request:Request,db=Depends(get_db)):
        inv=invitation(db,raw); application=editable(db,inv); data=validate(await json_body(request))
        if not application:
            application=Application(invitation_id=inv.id,data_json=dumps(data)); db.add(application); db.flush()
            inv.first_submitted_at=now(); inv.access_ends_at=(datetime.now(timezone.utc)+timedelta(days=c.limit('INVITE_UNISSUED_RESULT_DAYS',30))).isoformat()
        else:
            application.data_json=dumps(data); application.status='PENDING'; application.submitted_at=now(); application.rejection_reason=None
        version=db.scalar(select(func.count()).select_from(Revision).where(Revision.application_id==application.id))+1
        snapshot=dict(data,attachment_ids=[a.id for a in attachment_rows(db,inv)])
        db.add(Revision(application_id=application.id,version_no=version,snapshot_json=dumps(snapshot)))
        audit(db,request,None,'提交申请',application.id,{'version':version}); db.commit(); return {'ok':True}

    @app.post('/api/apply/{raw}/attachments')
    async def upload(raw:str,request:Request,kind:str=Form(...),file:UploadFile=File(...),db=Depends(get_db)):
        inv=invitation(db,raw); editable(db,inv)
        if kind not in ('WORKER_PHOTO','APPROVAL_LETTER','CONSTRUCTION_PLAN'): raise HTTPException(422,'材料类别无效')
        photo=kind=='WORKER_PHOTO'; existing=db.scalars(select(Attachment).where(Attachment.invitation_id==inv.id)).all()
        count=sum((a.kind=='WORKER_PHOTO')==photo for a in existing)
        maximum=c.limit('MAX_PHOTO_COUNT',10) if photo else c.limit('MAX_PDF_COUNT_PER_APPLICATION',5)
        if count>=maximum: raise HTTPException(422,f'最多上传{maximum}份'+('照片' if photo else 'PDF'))
        max_bytes=c.limit('MAX_PHOTO_MB',10)*1024**2 if photo else c.limit('MAX_PDF_MB',200)*1024**2
        key=uuid.uuid4().hex; target=c.UPLOADS/key; size=0; sha=hashlib.sha256()
        try:
            with target.open('wb') as out:
                while chunk:=await file.read(1024*1024):
                    size+=len(chunk)
                    if size>max_bytes: raise HTTPException(413,f'文件超过{max_bytes//1024**2} MB限制')
                    sha.update(chunk); out.write(chunk)
            if not size: raise HTTPException(422,'文件为空')
            if photo:
                with Image.open(target) as im:
                    if im.format not in ('JPEG','PNG'): raise ValueError()
                    mime=Image.MIME[im.format]; im.verify()
            else:
                with target.open('rb') as check:
                    if check.read(5)!=b'%PDF-': raise ValueError()
                reader=PdfReader(target,strict=True)
                if reader.is_encrypted: raise HTTPException(422,'不支持加密PDF，请先解除密码保护')
                len(reader.pages); mime='application/pdf'
            row=Attachment(invitation_id=inv.id,kind=kind,mime=mime,size_bytes=size,sha256=sha.hexdigest(),storage_key=key,original_name=Path(file.filename or 'attachment').name[:255])
            db.add(row); db.flush(); audit(db,request,None,'上传附件',row.id,{'invitation_id':inv.id,'bytes':size}); db.commit()
            return {'id':row.id,'name':row.original_name}
        except HTTPException:
            target.unlink(missing_ok=True); raise
        except Exception:
            target.unlink(missing_ok=True); raise HTTPException(422,'文件格式或内容无效，请上传真实的PNG/JPEG图片或PDF')
        finally: await file.close()

    @app.delete('/api/apply/{raw}/attachments/{id}')
    def remove_attachment(raw:str,id:int,request:Request,db=Depends(get_db)):
        inv=invitation(db,raw); editable(db,inv); row=find(db,Attachment,id)
        if row.invitation_id!=inv.id: raise HTTPException(404,'附件不存在')
        # Historical revisions retain their private attachment objects after removal.
        revisions=db.scalars(select(Revision).join(Application,Revision.application_id==Application.id).where(Application.invitation_id==inv.id)).all()
        if any(id in json.loads(r.snapshot_json).get('attachment_ids',[]) for r in revisions):
            row.is_current=False; audit(db,request,None,'移除当前版本附件',id); db.commit(); return {'ok':True}
        key=row.storage_key; db.delete(row); audit(db,request,None,'删除未提交附件',id); db.commit(); (c.UPLOADS/key).unlink(missing_ok=True); return {'ok':True}

    @app.get('/api/apply/{raw}/permit-image')
    def applicant_image(raw:str,db=Depends(get_db)):
        inv=invitation(db,raw); application=db.scalar(select(Application).where(Application.invitation_id==inv.id))
        permit=db.scalar(select(Permit).where(Permit.application_id==application.id).order_by(Permit.id.desc())) if application else None
        if not permit: raise HTTPException(404,'尚未签发许可证')
        return FileResponse(c.IMAGES/permit.image_storage_key,media_type='image/png',filename=permit.permit_no+'.png')

    @app.get('/staff/applications')
    def apps_page(request:Request,q:str='',state:str='',user=Depends(staff),db=Depends(get_db)):
        rows=db.scalars(select(Application).order_by(Application.id.desc())).all()
        rows=[a for a in rows if (not state or a.status==state) and (not q or q.lower() in a.data_json.lower() or q==str(a.id))]
        return page(request,'applications.html',db,user=user,applications=rows,q=q,state=state)

    @app.get('/staff/applications/{id}')
    def app_detail(id:int,request:Request,user=Depends(staff),db=Depends(get_db)):
        application=find(db,Application,id); inv=find(db,Invitation,application.invitation_id)
        return page(request,'application.html',db,user=user,application=application,data=json.loads(application.data_json),attachments=attachment_rows(db,inv),revisions=db.scalars(select(Revision).where(Revision.application_id==id).order_by(Revision.version_no.desc())).all(),permit=db.scalar(select(Permit).where(Permit.application_id==id).order_by(Permit.id.desc())))

    @app.get('/api/staff/attachments/{id}')
    def download_attachment(id:int,user=Depends(staff),db=Depends(get_db)):
        row=find(db,Attachment,id); return FileResponse(c.UPLOADS/row.storage_key,media_type=row.mime,filename=row.original_name)

    @app.post('/api/staff/applications/{id}/approve')
    async def approve(id:int,request:Request,user=Depends(staff),db=Depends(get_db)):
        application=db.scalar(select(Application).where(Application.id==id).with_for_update())
        if not application: raise HTTPException(404,'申请不存在')
        existing=db.scalar(select(Permit).where(Permit.active_application_id==id))
        if existing: return {'id':existing.id}
        if application.status!='PENDING': raise HTTPException(409,'申请不在待审批状态')
        body=await json_body(request); data=validate(body.get('data') or json.loads(application.data_json))
        permit=issue(db,request,user,application,data); return {'id':permit.id}

    @app.post('/api/staff/applications/{id}/reject')
    async def reject(id:int,request:Request,user=Depends(staff),db=Depends(get_db)):
        application=db.scalar(select(Application).where(Application.id==id).with_for_update())
        if not application: raise HTTPException(404,'申请不存在')
        if application.status!='PENDING': raise HTTPException(409,'申请不在待审批状态')
        body=await json_body(request); reason=str(body.get('reason','')).strip()
        if not 1<=len(reason)<=1000: raise HTTPException(422,'请填写驳回原因，最多1000字')
        application.status='REJECTED'; application.rejection_reason=reason; application.reviewed_at=now(); application.reviewed_by=user.id
        audit(db,request,user,'驳回申请',id,{'reason':reason}); db.commit(); return {'ok':True}

    @app.get('/staff/permits')
    def permits_page(request:Request,q:str='',state:str='',start:str='',end:str='',issuer:str='',user=Depends(staff),db=Depends(get_db)):
        rows=db.scalars(select(Permit).order_by(Permit.id.desc())).all()
        names={u.id:u.username for u in db.scalars(select(User)).all()}
        rows=[p for p in rows if (not q or q.lower() in (p.permit_no+p.snapshot_json).lower()) and (not state or status(p)==state) and (not start or p.end_date>=start) and (not end or p.start_date<=end) and (not issuer or issuer==names.get(p.issued_by))]
        return page(request,'permits.html',db,user=user,permits=rows,q=q,state=state,start=start,end=end,issued_by=issuer,names=names)

    @app.get('/staff/permits/{id}')
    def permit_page(id:int,request:Request,user=Depends(staff),db=Depends(get_db)):
        permit=find(db,Permit,id)
        return page(request,'permit.html',db,user=user,permit=permit,data=json.loads(permit.snapshot_json),visits=db.scalar(select(func.count()).select_from(VerificationLog).where(VerificationLog.permit_id==id)))

    @app.get('/api/staff/permits/{id}/image')
    def permit_image(id:int,user=Depends(staff),db=Depends(get_db)):
        permit=find(db,Permit,id); return FileResponse(c.IMAGES/permit.image_storage_key,media_type='image/png',filename=permit.permit_no+'.png',content_disposition_type='inline')

    @app.post('/api/staff/permits/{id}/revoke')
    async def revoke(id:int,request:Request,user=Depends(staff),db=Depends(get_db)):
        p=db.scalar(select(Permit).where(Permit.id==id).with_for_update())
        if not p: raise HTTPException(404,'许可证不存在')
        body=await json_body(request); reason=str(body.get('reason','')).strip()
        if not 1<=len(reason)<=1000: raise HTTPException(422,'请填写撤销原因，最多1000字')
        if not p.revoked_at:
            p.revoked_at=now(); p.revoke_reason=reason; p.active_application_id=None
            audit(db,request,user,'撤销许可证',id,{'reason':reason}); db.commit()
        return {'ok':True}

    @app.post('/api/staff/permits/{id}/reissue')
    async def reissue(id:int,request:Request,user=Depends(staff),db=Depends(get_db)):
        old=find(db,Permit,id)
        if not old.revoked_at: raise HTTPException(409,'请先撤销旧许可证，再重新签发')
        application=db.scalar(select(Application).where(Application.id==old.application_id).with_for_update())
        active=db.scalar(select(Permit).where(Permit.active_application_id==application.id))
        if active: return {'id':active.id}
        body=await json_body(request); data=validate(body.get('data') or json.loads(old.snapshot_json))
        p=issue(db,request,user,application,data,old.id); return {'id':p.id}

    @app.get('/staff/users')
    def users_page(request:Request,user=Depends(admin),db=Depends(get_db)):
        return page(request,'users.html',db,user=user,users=db.scalars(select(User).where(User.deleted_at==None).order_by(User.id)).all())

    @app.post('/api/staff/users')
    async def add_user(request:Request,user=Depends(admin),db=Depends(get_db)):
        body=await json_body(request); username=str(body.get('username','')).strip(); password=str(body.get('password',''))
        if not 2<=len(username)<=50 or not username.isascii() or not username.replace('_','').isalnum(): raise HTTPException(422,'用户名应为2—50位英文字母、数字或下划线')
        if not 12<=len(password)<=128: raise HTTPException(422,'初始密码须为12—128位')
        row=User(username=username,password_hash=passwords.hash(password),role='APPROVER'); db.add(row); db.flush(); audit(db,request,user,'创建审批员',row.id); db.commit(); return {'ok':True}

    @app.post('/api/staff/users/{id}')
    async def update_user(id:int,request:Request,user=Depends(admin),db=Depends(get_db)):
        row=find(db,User,id)
        if row.deleted_at: raise HTTPException(404,'账号已删除')
        if row.role=='ADMIN': raise HTTPException(403,'此接口仅管理审批员；管理员请在个人密码页面修改')
        body=await json_body(request)
        if 'is_active' in body:
            if not isinstance(body['is_active'],bool): raise HTTPException(422,'账号状态无效')
            row.is_active=body['is_active']
        if body.get('password'):
            password=str(body['password'])
            if not 12<=len(password)<=128: raise HTTPException(422,'密码须为12—128位')
            row.password_hash=passwords.hash(password); row.must_change_password=True
        db.execute(delete(StaffSession).where(StaffSession.user_id==id))
        audit(db,request,user,'更新审批员',id,{'is_active':row.is_active,'password_reset':bool(body.get('password'))}); db.commit(); return {'ok':True}

    @app.get('/staff/settings')
    def settings_page(request:Request,user=Depends(admin),db=Depends(get_db)):
        return page(request,'settings.html',db,user=user,public_url=output_base(db,'public'),verify_url=output_base(db,'verify'),verify_display_prefix=urlparse(output_base(db,'verify')).hostname or '',retention=setting(db,'visit_log_retention_days',str(c.limit('VISIT_LOG_RETENTION_DAYS',360))))

    @app.put('/api/staff/settings/issuer')
    async def issuer_settings(request:Request,user=Depends(admin),db=Depends(get_db)):
        body=await json_body(request); name=str(body.get('issuer_name','')).strip()
        if not 1<=len(name)<=100: raise HTTPException(422,'签发单位名称须为1—100字')
        before=setting(db,'issuer_name',c.ISSUER)
        for key,value in [('issuer_name',name),('visit_log_retention_days',str(body.get('retention',360)))]:
            if key=='visit_log_retention_days' and (not value.isdigit() or not 1<=int(value)<=3650): raise HTTPException(422,'日志保留天数须为1—3650')
            row=db.get(Setting,key)
            if row: row.value=value
            else: db.add(Setting(key=key,value=value))
        audit(db,request,user,'修改平台设置',details={'old_issuer':before,'new_issuer':name}); db.commit(); return {'ok':True}

    @app.put('/api/staff/settings/access')
    async def access_settings(request:Request,user=Depends(admin),db=Depends(get_db)):
        body=await json_body(request)
        public_base=validate_base(body.get('public_base_url'))
        verify_base=validate_base(body.get('verify_base_url'))
        before={k:setting(db,k) for k in ['public_base_url','verify_base_url']}
        for key,value in [('public_base_url',public_base),('verify_base_url',verify_base)]: save_setting(db,key,value)
        audit(db,request,user,'修改输出网址前缀',details={'before':before,'after':{'public_base_url':public_base,'verify_base_url':verify_base,'verify_host':urlparse(verify_base).hostname}})
        db.commit(); return {'ok':True}

    @app.put('/api/staff/settings/branding')
    async def branding_settings(request:Request,user=Depends(admin),db=Depends(get_db)):
        body=await json_body(request); name=body.get('header_name')
        if not isinstance(name,str) or not 1<=len(name.strip())<=100: raise HTTPException(422,'单位名称须为1—100字')
        save_setting(db,'header_name',name.strip()); audit(db,request,user,'修改管理界面单位名称'); db.commit(); return {'ok':True}

    @app.post('/api/staff/settings/branding/logo')
    async def branding_logo_upload(request:Request,file:UploadFile=File(...),user=Depends(admin),db=Depends(get_db)):
        content=await file.read(5*1024*1024+1); await file.close()
        if len(content)>5*1024*1024: raise HTTPException(413,'标志图片不得超过5 MB')
        try:
            with Image.open(io.BytesIO(content)) as im:
                if im.format not in ('PNG','JPEG'): raise ValueError()
                im.verify()
        except Exception: raise HTTPException(422,'请上传真实PNG或JPEG标志图片')
        key=uuid.uuid4().hex; target=c.UPLOADS/key; target.write_bytes(content)
        try:
            save_setting(db,'header_logo',key); audit(db,request,user,'修改管理界面单位标志'); db.commit()
        except Exception:
            target.unlink(missing_ok=True); raise
        return {'ok':True}

    @app.delete('/api/staff/settings/branding/logo')
    def reset_branding_logo(request:Request,user=Depends(admin),db=Depends(get_db)):
        save_setting(db,'header_logo','')
        audit(db,request,user,'恢复默认管理界面标志');db.commit();return {'ok':True}

    @app.get('/api/staff/branding/logo')
    def branding_logo(user=Depends(staff),db=Depends(get_db)):
        key=setting(db,'header_logo')
        if not key: raise HTTPException(404,'尚未配置界面标志')
        with Image.open(c.UPLOADS/key) as im: mime=Image.MIME[im.format]
        return FileResponse(c.UPLOADS/key,media_type=mime)

    def filtered_logs(db,params):
        stmt=select(VerificationLog).order_by(VerificationLog.id.desc())
        for param,col in [('permit_id',VerificationLog.permit_id),('ip',VerificationLog.source_ip),('device_id',VerificationLog.anon_device_id),('device_type',VerificationLog.device_type),('status',VerificationLog.status_seen)]:
            value=params.get(param)
            if value:
                if param=='permit_id':
                    try: value=int(value)
                    except ValueError: raise HTTPException(422,'许可证ID须为数字')
                stmt=stmt.where(col==value)
        for param,after in [('start',True),('end',False)]:
            if params.get(param):
                try: day=date.fromisoformat(params[param])
                except ValueError: raise HTTPException(422,'筛选日期无效')
                boundary=datetime.combine(day+timedelta(days=0 if after else 1),time.min,c.TZ).astimezone(timezone.utc).isoformat()
                stmt=stmt.where(VerificationLog.visited_at>=boundary if after else VerificationLog.visited_at<boundary)
        return stmt

    def filtered_actions(db,params):
        stmt=select(AuditLog).order_by(AuditLog.id.desc())
        if params.get('action'): stmt=stmt.where(AuditLog.action==params['action'])
        if params.get('actor_id'):
            try: actor=int(params['actor_id'])
            except ValueError: raise HTTPException(422,'操作人ID须为数字')
            stmt=stmt.where(AuditLog.actor_id==actor)
        for param,after in [('start',True),('end',False)]:
            if params.get(param):
                try: day=date.fromisoformat(params[param])
                except ValueError: raise HTTPException(422,'筛选日期无效')
                boundary=datetime.combine(day+timedelta(days=0 if after else 1),time.min,c.TZ).astimezone(timezone.utc).isoformat()
                stmt=stmt.where(AuditLog.created_at>=boundary if after else AuditLog.created_at<boundary)
        return stmt

    @app.get('/staff/audit')
    def audit_page(request:Request,tab:str='visits',user=Depends(staff),db=Depends(get_db)):
        logs=db.scalars(filtered_logs(db,request.query_params).limit(500)).all()
        actions=db.scalars(filtered_actions(db,request.query_params).limit(500)).all()
        return page(request,'audit.html',db,user=user,logs=logs,actions=actions,tab=tab,filters=request.query_params,names={u.id:u.username for u in db.scalars(select(User)).all()},numbers={p.id:p.permit_no for p in db.scalars(select(Permit)).all()})

    @app.get('/api/staff/verification-logs')
    def log_api(request:Request,user=Depends(staff),db=Depends(get_db)):
        return [{col.name:getattr(row,col.name) for col in VerificationLog.__table__.columns} for row in db.scalars(filtered_logs(db,request.query_params).limit(500)).all()]

    @app.get('/api/staff/verification-logs/export')
    def export(request:Request,user=Depends(admin),db=Depends(get_db)):
        stream=io.StringIO(); writer=csv.writer(stream)
        columns=[col.name for col in VerificationLog.__table__.columns]; writer.writerow(columns)
        for row in db.scalars(filtered_logs(db,request.query_params)).yield_per(500):
            values=[]
            for col in columns:
                value=getattr(row,col)
                if col=='visited_at': value=local(value)
                if isinstance(value,str) and value.startswith(('=','+','-','@','\t','\r')): value="'"+value
                values.append(value)
            writer.writerow(values)
        audit(db,request,user,'导出核验日志'); db.commit()
        return Response('\ufeff'+stream.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="verification-logs.csv"'})

    @app.get('/api/staff/audit-logs/export')
    def export_actions(request:Request,user=Depends(admin),db=Depends(get_db)):
        stream=io.StringIO(); writer=csv.writer(stream)
        columns=[col.name for col in AuditLog.__table__.columns]; writer.writerow(columns)
        for row in db.scalars(filtered_actions(db,request.query_params)).yield_per(500):
            values=[]
            for col in columns:
                value=local(getattr(row,col)) if col=='created_at' else getattr(row,col)
                if isinstance(value,str) and value.startswith(('=','+','-','@','\t','\r')): value="'"+value
                values.append(value)
            writer.writerow(values)
        audit(db,request,user,'导出后台操作日志'); db.commit()
        return Response('\ufeff'+stream.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="audit-logs.csv"'})

    return app

def create_verify_app():
    app=FastAPI(title='校园电子施工许可证 · 匿名核验',docs_url=None,redoc_url=None,openapi_url=None,lifespan=license_lifespan)
    app.mount('/verify/assets',StaticFiles(directory=c.ROOT/'app/verify_static'),name='verify-assets')

    @app.middleware('http')
    async def headers(request,call_next):
        response=await call_next(request)
        response.headers.update({'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Content-Security-Policy':"default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self'; frame-ancestors 'none'"})
        return response

    @app.get('/verify/{raw}',response_class=HTMLResponse)
    def verify(raw:str,request:Request,db=Depends(get_db)):
        permit=db.scalar(select(Permit).where(Permit.verify_token_hash==digest(raw))) if len(raw)<=100 else None
        state=status(permit); http_status=200 if permit else 404
        anonymous=request.cookies.get('anon_device_id','')
        try: anonymous=str(uuid.UUID(anonymous))
        except ValueError: anonymous=str(uuid.uuid4())
        ua=request.headers.get('user-agent',''); parsed=parse_ua(ua[:8192])
        db.add(VerificationLog(permit_id=permit.id if permit else None,anon_device_id=anonymous,source_ip=ip(request),user_agent=ua,device_type='手机' if parsed.is_mobile else '平板' if parsed.is_tablet else '电脑' if parsed.is_pc else '未知',browser_name=parsed.browser.family if parsed.browser.family not in ('Other','') else '未知',browser_version=parsed.browser.version_string or '未知',os_name=parsed.os.family if parsed.os.family not in ('Other','') else '未知',os_version=parsed.os.version_string or '未知',status_seen=state,http_status=http_status)); db.commit()
        # Explicit public allowlist: no phone, photos, remark or attachment metadata.
        snapshot=json.loads(permit.snapshot_json) if permit else {}
        data={key:snapshot.get(key,'') for key in ['issuer_name','unit_name','reason','leader_name','construction_location','start_date','end_date','issued_at']}
        res=templates.TemplateResponse(request=request,name='verify.html',context={'permit':permit,'data':data,'state':state,'state_label':STATUSES[state],'checked_at':local(now()),'server_now':now(),'unit_name':setting(db,'header_name','示例单位')},status_code=http_status)
        res.set_cookie('anon_device_id',anonymous,httponly=True,samesite='lax',secure=c.SECURE,max_age=365*86400,path='/verify')
        return res
    return app

approval_app=create_approval_app()
verify_app=create_verify_app()
