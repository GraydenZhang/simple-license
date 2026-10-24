"""Release builds require explicitly provisioned public trust anchors."""
import base64
import json
import re
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

keys = json.loads(Path('app/license_public_keys.json').read_text('ascii'))
if not isinstance(keys, dict) or not keys:
    raise SystemExit('Release blocked: provision developer public keys in app/license_public_keys.json first')
for name, value in keys.items():
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', name):
        raise SystemExit('Invalid public key ID')
    Ed25519PublicKey.from_public_bytes(base64.b64decode(value, validate=True))
print('Public trust anchors validated; no signing private keys needed for build.')
