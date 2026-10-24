"""Local verification only. No bypass switches or externally supplied trust roots."""
import base64
import json
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import HTTPException
from .license_protocol import PRODUCT, canonical, fingerprint, instant, strict_json

@dataclass(frozen=True)
class LicenseState:
    valid: bool
    code: str
    customer: str = ''
    license_id: str = ''
    expires_at: str = ''

def device_fingerprint():
    path = Path(os.getenv('HOST_MACHINE_ID_FILE', '/run/host-machine-id'))
    with path.open('r', encoding='ascii') as stream:
        return fingerprint(stream.read(1024))

def public_keys():
    # This file is compiled into the release's trusted resources, never mounted.
    keys = json.loads((Path(__file__).parent / 'license_public_keys.json').read_text('ascii'))
    if not isinstance(keys, dict):
        raise ValueError('Invalid public trust anchors')
    return keys

def check_license(now=None):
    try:
        path = Path(os.getenv('LICENSE_FILE', '/run/school-permit-license/license.lic'))
        with path.open('rb') as stream:
            raw = stream.read(16385)
        if len(raw) > 16384:
            return LicenseState(False, 'malformed_license')
        envelope = strict_json(raw)
        if not isinstance(envelope, dict) or set(envelope) != {'payload', 'signature'}:
            return LicenseState(False, 'malformed_license')
        payload = envelope['payload']
        data = canonical(payload)
        encoded_key = public_keys().get(payload['key_id'])
        if not encoded_key:
            return LicenseState(False, 'unknown_key')
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(encoded_key, validate=True))
        signature = base64.b64decode(envelope['signature'], validate=True)
        key.verify(signature, data)
        if payload['product'] != PRODUCT:
            return LicenseState(False, 'wrong_product')
        if payload['fingerprint'] != device_fingerprint():
            return LicenseState(False, 'wrong_device')
        current = now or datetime.now(timezone.utc)
        if current < instant(payload['issued_at']):
            return LicenseState(False, 'not_yet_valid')
        if current >= instant(payload['expires_at']):
            return LicenseState(False, 'expired')
        return LicenseState(True, 'valid', payload['customer'], payload['license_id'], payload['expires_at'])
    except FileNotFoundError:
        return LicenseState(False, 'missing_license_or_device_id')
    except InvalidSignature:
        return LicenseState(False, 'invalid_signature')
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        return LicenseState(False, 'invalid_license_or_device_id')

def require_license():
    state = check_license()
    if not state.valid:
        raise HTTPException(503, {'code': 'LICENSE_READ_ONLY', 'reason': state.code,
                                  'message': '软件授权无效，当前仅允许查询、备份、导出及账户安全操作，请联系软件提供方更新授权。'})

@asynccontextmanager
async def license_lifespan(app):
    state = check_license()
    app.state.license_at_startup = state
    logger = logging.getLogger('school_permit.license')
    (logger.info if state.valid else logger.warning)('License startup status: %s', state.code)
    # Never stop the process: historical data must remain accessible.
    yield
