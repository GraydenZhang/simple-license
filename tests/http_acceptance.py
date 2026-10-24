"""Real HTTP tests against tests.qa_server. Streams exact-size PDFs from disk."""
import concurrent.futures
import io
import json
from pathlib import Path
import re
import time
import sys
import os
import httpx
from pypdf import PdfWriter

ROOT=Path(__file__).resolve().parent.parent
OUT=Path(os.getenv('QA_REPORT_DIR',str(ROOT/'artifacts')))
OUT.mkdir(parents=True,exist_ok=True)

def exact_pdf(path,target):
    parts=[b'%PDF-1.7\n',b'1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n',b'2 0 obj\n<< /Type /Pages /Count 1 /Kids [3 0 R] >>\nendobj\n',b'3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>\nendobj\n']
    count=target-1000
    for _ in range(5):
        header=b''.join(parts)+f'4 0 obj\n<< /Length {count} >>\nstream\n'.encode()
        offsets=[]; pos=len(parts[0])
        for part in parts[1:]: offsets.append(pos); pos+=len(part)
        offsets.append(pos)
        end=b'\nendstream\nendobj\n'
        xref=len(header)+count+len(end)
        tail=end+b'xref\n0 5\n0000000000 65535 f \n'+b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets)+f'trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
        actual=len(header)+count+len(tail)
        if actual==target: break
        count+=target-actual
    with path.open('wb') as file:
        file.write(header); remaining=count; chunk=b' '*1024**2
        while remaining:
            n=min(remaining,len(chunk)); file.write(chunk[:n]); remaining-=n
        file.write(tail)
    assert path.stat().st_size==target

def main():
    report={}; client=httpx.Client(base_url='http://127.0.0.1:15101',timeout=180,trust_env=False)
    result=client.post('/api/staff/session',json={'username':'qa_admin','password':'Isolated-QA-Secret-2026'}); result.raise_for_status()
    page=client.get('/staff/dashboard'); csrf=re.search(r'name="csrf-token" content="([^"]+)"',page.text).group(1)
    client.headers.update({'X-CSRF-Token':csrf,'X-Requested-With':'campus-permits'})
    def invitation(): return client.post('/api/staff/invitations',json={'label':'真实HTTP边界验收'}).json()['url'].split('/')[-1]
    raw=invitation(); other=invitation()
    large=OUT/'exact-200mb.pdf'; excess=OUT/'over-200mb.pdf'
    exact_pdf(large,200*1024**2); exact_pdf(excess,200*1024**2+1)
    begin=time.monotonic()
    with large.open('rb') as file:
        result=client.post(f'/api/apply/{raw}/attachments',data={'kind':'APPROVAL_LETTER'},files={'file':('exact-200mb.pdf',file,'application/pdf')})
    assert result.status_code==200,result.text
    report['exact_200_MiB']={'result':'PASS','bytes':large.stat().st_size,'seconds':round(time.monotonic()-begin,2)}
    with excess.open('rb') as file:
        result=client.post(f'/api/apply/{other}/attachments',data={'kind':'APPROVAL_LETTER'},files={'file':('over-200mb.pdf',file,'application/pdf')})
    assert result.status_code==413,result.text
    report['200_MiB_plus_1_byte']={'result':'PASS','http':result.status_code}
    buffer=io.BytesIO(); pdf=PdfWriter(); pdf.add_blank_page(width=100,height=100); pdf.write(buffer)
    for n in range(4):
        result=client.post(f'/api/apply/{raw}/attachments',data={'kind':'CONSTRUCTION_PLAN'},files={'file':(f'{n}.pdf',buffer.getvalue(),'application/pdf')})
        assert result.status_code==200,result.text
    result=client.post(f'/api/apply/{raw}/attachments',data={'kind':'APPROVAL_LETTER'},files={'file':('six.pdf',buffer.getvalue(),'application/pdf')})
    assert result.status_code==422
    report['sixth_pdf']={'result':'PASS','http':422}
    data={'unit_name':'HTTP并发验收（测试）','reason':'重复审批事务验收','leader_name':'测试负责人','leader_phone':'0000-00000000','location_type':'OTHER','other_location_text':'测试实验室','start_date':'2026-10-24','end_date':'2026-10-25'}
    assert client.post(f'/api/apply/{raw}/submit',json=data).status_code==200
    listing=client.get('/staff/applications').text
    # The newest matching application is the one just submitted in this isolated server.
    ids=re.findall(r'href="/staff/applications/(\d+)"',listing); aid=max(map(int,ids))
    def approve_once(_):
        with httpx.Client(base_url=str(client.base_url),headers=dict(client.headers),cookies=client.cookies,timeout=60,trust_env=False) as concurrent_client:
            result=concurrent_client.post(f'/api/staff/applications/{aid}/approve',json={})
            assert result.status_code==200,result.text
            return result.json()['id']
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool: issued=list(pool.map(approve_once,range(2)))
    assert issued[0]==issued[1]
    report['concurrent_approve']={'result':'PASS','responses':2,'unique_permit_ids':len(set(issued))}
    # New process loading the same DB demonstrates persistent state independently of HTTP sessions.
    import subprocess,os
    qa_data=Path(os.getenv('QA_DATA_DIR',str(ROOT/'artifacts/browser-qa')))
    env=dict(os.environ,DATA_DIR=str(qa_data),DATABASE_URL='sqlite:///'+str(qa_data/'qa.db'))
    check=subprocess.run([sys.executable,'-c','from app.models import Session,Permit; from sqlalchemy import select; db=Session(); assert db.scalar(select(Permit)) is not None; print("PERSISTENCE_PASS")'],cwd=ROOT,env=env,capture_output=True,text=True)
    assert check.returncode==0,check.stderr
    report['independent_process_persistence']={'result':'PASS'}
    (OUT/'http-acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(report,ensure_ascii=False))
    # Generated transport inputs are disposable. Accepted private attachment stays in QA storage.
    large.unlink(); excess.unlink(); client.close()

if __name__=='__main__': main()
