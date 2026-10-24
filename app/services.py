import hashlib
import io
import ipaddress
import json
import secrets
import textwrap
from datetime import datetime, date, time, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
import os
import qrcode
from PIL import Image, ImageDraw, ImageFont
from fastapi import HTTPException
from . import config as c
from .models import AuditLog, now

STATUSES = {'VALID':'有效','NOT_STARTED':'尚未生效','OUTSIDE_HOURS':'当日非允许施工时间','EXPIRED':'已过期','REVOKED':'已撤销','NOT_FOUND':'凭证不存在'}
DORMS = ['一','二','三','四','五','六','七','八','九','十','十一']
def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def token(): return secrets.token_urlsafe(32)
def dumps(value): return json.dumps(value, ensure_ascii=False)
def local(value):
    return datetime.fromisoformat(value).astimezone(c.TZ).strftime('%Y-%m-%d %H:%M:%S') if value else '—'
def ip(request):
    remote = request.client.host if request.client else 'unknown'
    try:
        nets = [ipaddress.ip_network(n) for n in c.TRUSTED_PROXIES]
        if any(ipaddress.ip_address(remote) in n for n in nets):
            # Walk from the trusted proxy towards the first untrusted hop.
            chain = [x.strip() for x in request.headers.get('x-forwarded-for','').split(',') if x.strip()] + [remote]
            for hop in reversed(chain):
                addr = ipaddress.ip_address(hop)
                if not any(addr in net for net in nets): return str(addr)
    except ValueError: pass
    return remote
def audit(db, request, user, action, entity_id=None, details=None):
    db.add(AuditLog(actor_id=user.id if user else None, action=action, entity_id=entity_id, details_json=dumps(details or {}), source_ip=ip(request)))
def status(p, at=None):
    if not p: return 'NOT_FOUND'
    if p.revoked_at: return 'REVOKED'
    current = (at or datetime.now(timezone.utc)).astimezone(c.TZ)
    start, end = date.fromisoformat(p.start_date), date.fromisoformat(p.end_date)
    if current.date() < start: return 'NOT_STARTED'
    if current.date() > end or (current.date() == end and current.time() >= time(18,30)): return 'EXPIRED'
    if not time(8) <= current.time() < time(18,30): return 'OUTSIDE_HOURS'
    return 'VALID'
def validate(data):
    if not isinstance(data,dict): raise HTTPException(422,'申请数据格式无效')
    clean = {}
    for name, label, maxlen in [('unit_name','单位名称',100),('reason','事由',150),('leader_name','项目负责人',50),('leader_phone','负责人联系电话',30)]:
        value = data.get(name,'')
        if not isinstance(value,str): raise HTTPException(422, f'{label}必须为文本')
        value = value.strip()
        if not value or len(value)>maxlen or (name=='leader_phone' and len(value)<5):
            raise HTTPException(422, f'{label}长度不符合要求')
        clean[name] = value
    clean['location_type'] = data.get('location_type')
    if clean['location_type']=='DORMITORY':
        selections=data.get('dormitories',[])
        if not isinstance(selections,list) or any(type(x) is not int and (not isinstance(x,str) or not x.isascii() or not x.isdigit()) for x in selections):
            raise HTTPException(422,'楼栋选择无效')
        try: dorms = sorted(set(int(x) for x in selections))
        except (ValueError,TypeError): raise HTTPException(422,'楼栋选择无效')
        if not dorms or any(n<1 or n>11 for n in dorms): raise HTTPException(422,'请选择一栋至十一栋中的至少一栋')
        clean.update(dormitories=dorms, other_location_text='', construction_location='宿舍：'+'、'.join(DORMS[n-1]+'栋' for n in dorms))
    elif clean['location_type']=='OTHER':
        value = data.get('other_location_text','')
        if not isinstance(value,str): raise HTTPException(422,'施工地点必须为文本')
        value = value.strip()
        if not 1<=len(value)<=500: raise HTTPException(422,'请填写具体施工地点（最多500字）')
        clean.update(dormitories=[],other_location_text=value, construction_location=value)
    else: raise HTTPException(422,'请选择施工地点类型')
    try:
        start, end = date.fromisoformat(data.get('start_date','')), date.fromisoformat(data.get('end_date') or data.get('start_date',''))
        if end<start: raise ValueError()
    except (ValueError,TypeError): raise HTTPException(422,'施工日期无效或结束日期早于开始日期')
    clean.update(start_date=start.isoformat(),end_date=end.isoformat())
    count = data.get('worker_count')
    if count not in (None,''):
        try:
            if str(int(count))!=str(count) or not 1<=int(count)<=9999: raise ValueError()
        except (ValueError,TypeError): raise HTTPException(422,'施工人数应为1—9999的整数')
        count=int(count)
    else: count=None
    remark=data.get('remark','')
    if not isinstance(remark,str): raise HTTPException(422,'备注必须为文本')
    remark=remark.strip()
    if len(remark)>1000: raise HTTPException(422,'备注最多1000字')
    clean.update(worker_count=count,remark=remark)
    return clean
def qr_data(url):
    import base64
    out=io.BytesIO(); qrcode.make(url).save(out,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()
def font(size,bold=False):
    candidates=[os.getenv('BOLD_FONT_PATH' if bold else 'FONT_PATH',''), 'C:/Windows/Fonts/msyhbd.ttc' if bold else 'C:/Windows/Fonts/msyh.ttc','/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc' if bold else '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc']
    if bold: candidates.extend([os.getenv('FONT_PATH',''),'C:/Windows/Fonts/msyh.ttc','/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'])
    for p in candidates:
        if p and Path(p).exists(): return ImageFont.truetype(p,size)
    raise RuntimeError('未找到中文字体，请配置 FONT_PATH')
def make_image(snapshot, number, url, key):
    # Portrait 9:16. Layout is designed for a phone, not rotated from landscape.
    image=Image.new('RGB',(1080,1920),'#f2f6f5');draw=ImageDraw.Draw(image)
    draw.rectangle((0,0,1080,16),fill='#146c5c')
    draw.rounded_rectangle((24,24,1056,1896),radius=26,fill='white',outline='#d5e2dc',width=2)
    def wrap(value,size,width,bold=False):
        value=' '.join(value.split());lines=[];line='';face=font(size,bold)
        for char in value:
            if line and draw.textlength(line+char,font=face)>width:lines.append(line);line=''
            line+=char
        if line:lines.append(line)
        return lines or ['']
    def center(value,y,size,bold=False,color='#193b34'):
        draw.text((540,y),value,font=font(size,bold),fill=color,anchor='mt')
        return draw.textbbox((540,y),value,font=font(size,bold),anchor='mt')[3]
    y=65
    for line in wrap(snapshot['issuer_name'],32,920):center(line,y,32);y+=44
    y+=18;center('校内电子施工准入凭证',y,62,True);y+=88
    center(number,y,28,color='#526e64');y+=60
    draw.line((70,y,1010,y),fill='#d9e5de',width=2);y+=28
    fields=[('单位名称',snapshot['unit_name']),('事由',snapshot['reason']),('项目负责人',snapshot['leader_name']+'  '+snapshot['leader_phone']),('施工范围 / 地点',snapshot['construction_location']),('施工日期',snapshot['start_date']+' 至 '+snapshot['end_date']),('每日允许时段','08:00—18:30（北京时间）')]
    for size in range(48,9,-1):
        rows=[(label,wrap(value,size,920)) for label,value in fields]
        label_h=min(38,max(19,size));line_h=size+12;gap=min(22,max(8,size//2))
        height=sum(label_h+len(lines)*line_h+gap for _,lines in rows)
        if y+height<=1040:break
    for label,lines in rows:
        draw.text((80,y),label,font=font(min(size,30)),fill='#536f63');y+=label_h
        for line in lines:
            draw.text((80,y),line,font=font(size),fill='#183e33');y+=line_h
        y+=gap
    # Measure the actual notice first so its panel has balanced padding, not a fixed empty tail.
    # These two instructions stay above the QR and remain large on phone displays.
    instruction='请连接授权现场网络 后扫码核验'
    notice_y=1082+len(wrap(instruction,44,920,True))*56+9+54
    expected='“'+(urlparse(url).hostname or '')+'”'
    for notice_size in range(56,13,-1):
        notice=wrap(expected,notice_size,900,True)
        if notice_y+len(notice)*(notice_size+8)<=1290:break
    # Keep the panel's lower inset to 18px even for short IP/host names.
    notice_bottom=notice_y+(len(notice)-1)*(notice_size+8)+draw.textbbox((540,0),notice[-1],font=font(notice_size,True),anchor='mt')[3]
    panel_bottom=min(1302,notice_bottom+18)
    draw.rounded_rectangle((58,1060,1022,panel_bottom),radius=18,fill='#edf6f1',outline='#d4e7db',width=2)
    text_y=1082
    for line in wrap(instruction,44,920,True):center(line,text_y,44,True,color='#124d3b');text_y+=56
    text_y+=9
    center('扫码后请确认网址开头为',text_y,40,True);text_y+=54
    for line in notice:center(line,text_y,notice_size,True,color='#a23d2a');text_y+=notice_size+8
    qr=qrcode.QRCode(border=4,box_size=1);qr.add_data(url);qr.make(fit=True)
    qr_top=panel_bottom+4
    modules=qr.modules_count+8;box=max(1,min(530,1810-qr_top)//modules)
    code=qr.make_image(fill_color='black',back_color='white').convert('RGB').resize((modules*box,modules*box),Image.Resampling.NEAREST)
    image.paste(code,((1080-code.width)//2,qr_top))
    footer_y=qr_top+code.height+8
    center('签发时间：'+local(snapshot['issued_at']),footer_y,24,color='#536f63')
    center('仅限校内使用，有效状态请以扫码页面实时显示为准',footer_y+38,30,True)
    image.save(c.IMAGES/key,format='PNG')
