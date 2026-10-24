import base64
import json
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import pytest
from fastapi import HTTPException
from sqlalchemy import select, func
from app import licensing
from app.license_protocol import canonical, fingerprint, strict_json
from app.models import Session, Invitation
from .conftest import invite, issue, verify_token, authenticate

def test_fingerprint_stable_private_and_product_bound(licensed_runtime):
    value = licensing.device_fingerprint()
    assert value == fingerprint('  0123456789ABCDEF0123456789ABCDEF\n')
    assert '0123456789abcdef' not in value
    assert value != fingerprint('1123456789abcdef0123456789abcdef')
    for bad in ('', 'uninitialized', '0' * 32, 'f' * 33):
        with pytest.raises(ValueError):
            fingerprint(bad)
    result = subprocess.run([sys.executable, 'school_permit.py', 'fingerprint'], capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == value

def test_canonical_format_exact(licensed_runtime):
    payload = licensed_runtime['payload']
    expected = b'school-permit/license/v1\x00' + json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode('ascii')
    assert canonical(dict(reversed(list(payload.items())))) == expected
    assert b'\\u9694' in expected
    for raw in ('{"x":1,"x":2}', '{"payload":{"x":1,"x":2}}'):
        with pytest.raises(ValueError): strict_json(raw)
    with pytest.raises(ValueError): canonical(dict(payload, version=True))

@pytest.mark.parametrize('change,code', [
    ({'product': 'other'}, 'wrong_product'),
    ({'fingerprint': fingerprint('f' * 32)}, 'wrong_device'),
    ({'expires_at': '2021-01-01T00:00:00Z'}, 'expired'),
    ({'issued_at': '2090-01-01T00:00:00Z'}, 'not_yet_valid'),
    ({'key_id': 'unknown'}, 'unknown_key'),
])
def test_signed_license_constraints(licensed_runtime, change, code):
    licensed_runtime['write'](**change)
    assert licensing.check_license().code == code

def test_signature_tampering_and_bad_files(licensed_runtime):
    path = licensed_runtime['path']
    data = json.loads(path.read_text('utf-8'))
    data['payload']['customer'] = 'tampered'
    path.write_text(json.dumps(data), encoding='utf-8')
    assert licensing.check_license().code == 'invalid_signature'
    for content in ('null', '[]', '{}', 'not json', 'x' * 16385):
        path.write_text(content)
        assert not licensing.check_license().valid
    licensed_runtime['write']()
    licensed_runtime['machine'].unlink()
    assert not licensing.check_license().valid
    path.unlink()
    assert not licensing.check_license().valid

def test_exact_expiration_and_atomic_reload(licensed_runtime):
    licensed_runtime['write'](expires_at='2030-01-01T00:00:00Z')
    assert licensing.check_license(datetime(2029,12,31,23,59,59,tzinfo=timezone.utc)).valid
    assert licensing.check_license(datetime(2030,1,1,tzinfo=timezone.utc)).code == 'expired'
    replacement = licensed_runtime['path'].with_suffix('.new')
    licensed_runtime['write']()
    replacement.write_bytes(licensed_runtime['path'].read_bytes())
    licensed_runtime['write'](expires_at='2021-01-01T00:00:00Z')
    assert not licensing.check_license().valid
    replacement.replace(licensed_runtime['path'])
    assert licensing.check_license().valid

def test_expiration_preserves_read_export_verify_backup(staff, verify, sample, licensed_runtime, tmp_path):
    raw = invite(staff)
    permit = issue(staff, raw, sample)
    qr = verify_token(staff, permit)
    licensed_runtime['path'].unlink()
    for url, body in [('/api/staff/invitations', {}),
                      (f'/api/staff/applications/1/approve', {}),
                      (f'/api/staff/permits/{permit}/reissue', {}),
                      ('/api/apply/' + raw + '/submit', sample)]:
        response = staff.post(url, json=body, headers={'X-Requested-With': 'campus-permits'})
        assert response.status_code == 503, response.text
        assert response.json()['detail']['code'] == 'LICENSE_READ_ONLY'
    for url in ['/staff/permits', f'/api/staff/permits/{permit}/image',
                '/api/staff/audit-logs/export', '/api/staff/verification-logs/export']:
        assert staff.get(url).status_code == 200
    assert verify.get('/verify/' + qr).status_code == 200
    assert staff.get('/api/staff/license').json()['valid'] is False
    result = subprocess.run([sys.executable, 'school_permit.py', 'backup', str(tmp_path / 'backup')], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'backup' / 'campus.db').exists()
    # Authentication still works after expiry, including first-password-change flow.
    staff.cookies.clear()
    assert staff.post('/api/staff/session', json={'username':'qa_admin','password':'Isolated-QA-Secret-2026'}).status_code == 200

def test_commit_rechecks_license(staff, licensed_runtime, monkeypatch):
    original = licensing.check_license
    calls = 0
    def check(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs) if calls == 1 else licensing.LicenseState(False, 'expired')
    monkeypatch.setattr(licensing, 'check_license', check)
    assert staff.post('/api/staff/invitations', json={}).status_code == 503
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Invitation)) == 0

def test_startup_invalid_is_read_only(client, licensed_runtime):
    from fastapi.testclient import TestClient
    from app.main import create_approval_app
    licensed_runtime['path'].unlink()
    app = create_approval_app()
    with TestClient(app) as session:
        assert not app.state.license_at_startup.valid
        assert session.get('/staff/login').status_code == 200
        assert session.post('/api/staff/invitations', json={}).status_code == 503

def test_key_rotation(licensed_runtime, monkeypatch):
    old_keys = licensing.public_keys()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    new = Ed25519PrivateKey.generate()
    encoded = base64.b64encode(new.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    monkeypatch.setattr(licensing, 'public_keys', lambda: {**old_keys, 'new-key': encoded})
    assert licensing.check_license().valid
    payload = dict(licensed_runtime['payload'], key_id='new-key')
    licensed_runtime['path'].write_text(json.dumps({'payload':payload,'signature':base64.b64encode(new.sign(canonical(payload))).decode()}))
    assert licensing.check_license().valid
    monkeypatch.setattr(licensing, 'public_keys', lambda: old_keys)
    assert licensing.check_license().code == 'unknown_key'

def test_developer_tool_encrypted_key_and_roundtrip(tmp_path, monkeypatch, licensed_runtime):
    from developer_tools import issuer
    monkeypatch.setattr(issuer, 'KEY_HOME', tmp_path / 'developer-private')
    monkeypatch.setattr(issuer, 'password', lambda: b'Test-only-passphrase-2026')
    monkeypatch.setattr(issuer.getpass, 'getpass', lambda _: 'Test-only-passphrase-2026')
    public = tmp_path / 'public.json'
    monkeypatch.setattr(sys, 'argv', ['issuer', 'keygen', '--key-id', 'test-real', '--public-out', str(public)])
    issuer.main()
    private = tmp_path / 'developer-private' / 'test-real.pem'
    assert b'ENCRYPTED PRIVATE KEY' in private.read_bytes()
    from cryptography.hazmat.primitives import serialization
    with pytest.raises(TypeError): serialization.load_pem_private_key(private.read_bytes(), None)
    path = tmp_path / 'issued.lic'
    monkeypatch.setattr(sys, 'argv', ['issuer','issue','--key-id','test-real','--customer','测试客户',
                        '--fingerprint',licensing.device_fingerprint(),'--expires-at','2099-01-01T00:00:00Z','--out',str(path)])
    issuer.main()
    monkeypatch.setenv('LICENSE_FILE', str(path))
    monkeypatch.setattr(licensing, 'public_keys', lambda: json.loads(public.read_text()))
    assert licensing.check_license().valid
    with pytest.raises(FileExistsError): issuer.main()

def test_compiled_migration_matches_alembic_schema():
    from app.models import Base, engine
    from app.schema_migrations import migrate
    from sqlalchemy import text, inspect
    Base.metadata.drop_all(engine)
    with engine.begin() as connection: connection.execute(text('DROP TABLE alembic_version'))
    migrate()
    migrate()
    with engine.connect() as connection:
        assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0003'
        assert 'users' in inspect(connection).get_table_names()
        assert connection.execute(text("SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 'audit_no_%'")).scalar() == 2

@pytest.mark.parametrize('hostname', ['127.0.0.1', 'a-long-campus-verification-hostname.example.com'])
def test_permit_footer_qr_after_layout_change(hostname, tmp_path, monkeypatch):
    from app.services import make_image
    from app import config
    from PIL import Image
    import zxingcpp
    monkeypatch.setattr(config, 'IMAGES', tmp_path)
    snapshot = dict(issuer_name='隔离学校管理人员',unit_name='隔离验收单位',reason='网络维护',leader_name='测试负责人',leader_phone='0000-00000000',construction_location='宿舍一栋',start_date='2026-10-24',end_date='2026-10-26',issued_at='2026-10-24T08:05:17+00:00')
    url='https://'+hostname+'/verify/'+'x'*43
    make_image(snapshot, 'PERMIT-TEST-PREVIEW', url, 'preview.png')
    image=Image.open(tmp_path/'preview.png')
    assert image.size == (1080,1920)
    decoded=zxingcpp.read_barcode(image)
    assert decoded and decoded.text == url
    # Footer ink stays inside the white card's lower safe margin.
    for y in range(1890,1895):
        assert all(image.getpixel((x,y)) == (255,255,255) for x in range(150,930))
