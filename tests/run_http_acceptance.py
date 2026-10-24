"""Full HTTP acceptance in a fresh temporary database; stops only children it creates."""
import os
import subprocess
import sys
import tempfile
import time
import socket
from pathlib import Path
import httpx

root=Path(__file__).resolve().parents[1]
report=root/'artifacts/permit-http-acceptance'
report.mkdir(parents=True,exist_ok=True)
qa=Path(tempfile.mkdtemp(prefix='school-permit-full-http-'))
env=dict(os.environ,QA_DATA_DIR=str(qa),QA_REPORT_DIR=str(report))
children=[]
logs=[]
try:
    for port in (15101,15102):
        with socket.socket() as probe:
            try:probe.bind(('127.0.0.1',port))
            except OSError:raise RuntimeError(f'QA port {port} is occupied; existing process preserved')
    for service in ('approval','verify'):
        log=(report/(service+'.log')).open('w',encoding='utf-8');logs.append(log)
        children.append(subprocess.Popen([sys.executable,'-m','tests.qa_server',service],cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT))
    for port in (15101,15102):
        for attempt in range(100):
            if any(child.poll() is not None for child in children):
                raise RuntimeError('QA server stopped; inspect isolated logs')
            try:
                httpx.get(f'http://127.0.0.1:{port}/',timeout=1,trust_env=False)
                break
            except (httpx.ConnectError,httpx.ConnectTimeout):
                time.sleep(.1)
        else:
            raise RuntimeError('QA startup timeout')
    subprocess.run([sys.executable,'-m','tests.http_acceptance'],cwd=root,env=env,check=True)
    # Exercise every staff template and the public applicant screen over real HTTP.
    import re,json
    with httpx.Client(base_url='http://127.0.0.1:15101',timeout=30,trust_env=False) as client:
        client.post('/api/staff/session',json={'username':'qa_admin','password':'Isolated-QA-Secret-2026'}).raise_for_status()
        dashboard=client.get('/staff/dashboard')
        csrf=re.search(r'name="csrf-token" content="([^"]+)"',dashboard.text).group(1)
        client.headers['X-CSRF-Token']=csrf
        forbidden=['CAMPUS WORK PERMIT','WORKSPACE OVERVIEW','APPLICATION INVITATIONS','APPLICATION REVIEW','CONSTRUCTION ACCESS APPLICATION','QUICK ACCESS','ACCESS & AUDIT TRAIL','STAFF PORTAL','ACCOUNT SECURITY','PERMIT DETAILS','ELECTRONIC PERMITS','PLATFORM SETTINGS','TEAM & ACCESS']
        paths=['/staff/login','/staff/password','/staff/dashboard','/staff/invitations','/staff/applications','/staff/permits','/staff/users','/staff/settings','/staff/audit']
        for prefix in ('applications','permits'):
            page=client.get('/staff/'+prefix)
            match=re.search(r'href="(/staff/'+prefix+r'/\d+)"',page.text)
            assert match
            paths.append(match.group(1))
        invitation=client.post('/api/staff/invitations',json={'label':'中文界面验收'});invitation.raise_for_status()
        url=invitation.json()['url'];paths.append(url)
        for path in paths:
            response=client.get(path);assert response.status_code==200,(path,response.status_code)
            assert all(text not in response.text for text in forbidden),path
        permit=client.get('/staff/permits').text
        assert re.search(r'PERMIT-\d{8}-[A-F0-9]{8}',permit)
        result={'pages_checked':len(paths),'english_decorations':'PASS','new_number_prefix':'PASS','isolated_data':str(qa),'applicant_url':url}
        (report/'ui-checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
        print(json.dumps(result,ensure_ascii=False))
finally:
    for child in children:
        if child.poll() is None:child.terminate()
    for child in children:
        try:child.wait(timeout=15)
        except subprocess.TimeoutExpired:child.kill();child.wait()
    for log in logs:log.close()
