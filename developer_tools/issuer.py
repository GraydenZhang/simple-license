"""Developer-only signing CLI. Never packaged in customer images or bundles."""
import argparse
import base64
import getpass
import json
import os
from pathlib import Path
import sys
import uuid
from datetime import datetime, timezone
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.license_protocol import PRODUCT, canonical, instant

KEY_HOME = Path.home() / '.school-permit-signing'

def password():
    value = getpass.getpass('Signing key passphrase (12+ characters): ')
    if len(value) < 12:
        raise ValueError('Passphrase must have at least 12 characters')
    return value.encode('utf-8')

def key_path(key_id):
    import re
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', key_id):
        raise ValueError('Invalid key ID')
    path = KEY_HOME / (key_id + '.pem')
    workspace = Path(__file__).resolve().parents[1]
    if path.resolve().is_relative_to(workspace):
        raise ValueError('Signing key directory must be outside the source workspace')
    return path

def write_new(path, data, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    generate = sub.add_parser('keygen')
    generate.add_argument('--key-id', required=True)
    generate.add_argument('--public-out', type=Path, required=True, help='New JSON public-key ring file')
    sign = sub.add_parser('issue')
    sign.add_argument('--key-id', required=True)
    sign.add_argument('--product', default=PRODUCT)
    sign.add_argument('--customer', required=True)
    sign.add_argument('--fingerprint', required=True)
    sign.add_argument('--issued-at', default=None)
    sign.add_argument('--expires-at', required=True, help='Exclusive UTC timestamp, e.g. 2027-10-24T16:00:00Z')
    sign.add_argument('--license-id', default=None)
    sign.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    path = key_path(args.key_id)
    if args.command == 'keygen':
        if path.exists() or args.public_out.exists():
            raise ValueError('Refusing to overwrite existing keys or public output')
        secret = password()
        if secret != getpass.getpass('Confirm passphrase: ').encode('utf-8'):
            raise ValueError('Passphrases do not match')
        KEY_HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
        private = Ed25519PrivateKey.generate()
        encoded = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.BestAvailableEncryption(secret))
        write_new(path, encoded)
        public = base64.b64encode(private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode('ascii')
        write_new(args.public_out, (json.dumps({args.key_id: public}, indent=2) + '\n').encode('ascii'), 0o644)
        print('Encrypted private key saved outside workspace; public key ring exported. Protect the signing directory with OS permissions.')
    else:
        private = serialization.load_pem_private_key(path.read_bytes(), password=password())
        if not isinstance(private, Ed25519PrivateKey):
            raise ValueError('Not an Ed25519 private key')
        payload = dict(version=1, key_id=args.key_id, product=args.product, customer=args.customer,
                       fingerprint=args.fingerprint, issued_at=args.issued_at or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                       expires_at=args.expires_at, license_id=args.license_id or str(uuid.uuid4()))
        signed = canonical(payload)
        signature = base64.b64encode(private.sign(signed)).decode('ascii')
        write_new(args.out, (json.dumps({'payload': payload, 'signature': signature}, ensure_ascii=True, indent=2) + '\n').encode('ascii'), 0o644)
        print('License issued: ' + payload['license_id'])

if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc))
