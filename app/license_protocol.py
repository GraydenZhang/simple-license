"""School Permit License v1: deterministic ASCII JSON, Ed25519, no network I/O."""
import hashlib
import json
import re
from datetime import datetime, timezone

PRODUCT = 'school-permit'
DOMAIN = b'school-permit/license/v1\x00'
FIELDS = {'version', 'key_id', 'product', 'customer', 'fingerprint', 'issued_at', 'expires_at', 'license_id'}

def canonical(payload):
    if not isinstance(payload, dict) or set(payload) != FIELDS:
        raise ValueError('Invalid license fields')
    if type(payload['version']) is not int or payload['version'] != 1:
        raise ValueError('Unsupported license version')
    for name in FIELDS - {'version'}:
        value = payload[name]
        if not isinstance(value, str) or not 1 <= len(value) <= 500 or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError('Invalid license string')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', payload['key_id']):
        raise ValueError('Invalid key ID')
    if not re.fullmatch(r'sp1-[0-9a-f]{64}', payload['fingerprint']):
        raise ValueError('Invalid fingerprint')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', payload['license_id']):
        raise ValueError('Invalid license ID')
    if instant(payload['issued_at']) >= instant(payload['expires_at']):
        raise ValueError('Invalid validity interval')
    return DOMAIN + json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('ascii')

def instant(value):
    if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', value):
        raise ValueError('Expected UTC YYYY-MM-DDTHH:MM:SSZ')
    return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)

def fingerprint(machine_id):
    normalized = machine_id.strip().lower()
    if not re.fullmatch(r'[0-9a-f]{32}', normalized) or normalized == '0' * 32:
        raise ValueError('Host machine-id must be 32 nonzero hexadecimal characters')
    return 'sp1-' + hashlib.sha256(b'school-permit/device/v1\x00' + normalized.encode('ascii')).hexdigest()

def strict_json(raw):
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise ValueError('Duplicate JSON key')
            obj[key] = value
        return obj
    return json.loads(raw, object_pairs_hook=pairs)
