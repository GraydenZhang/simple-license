"""Fail before final COPY if Nuitka includes source/secrets/dev content."""
from pathlib import Path
import sys

root = Path(sys.argv[1])
if not (root / 'school-permit').is_file():
    raise SystemExit('Compiled executable missing')
for path in root.rglob('*'):
    if not path.is_file():
        continue
    parts = {part.lower() for part in path.relative_to(root).parts}
    public_ca = path.relative_to(root).as_posix() == 'certifi/cacert.pem'
    if (path.suffix.lower() in {'.py', '.pyc', '.pyo', '.pem', '.key', '.lic', '.map', '.c', '.cpp', '.h', '.p12', '.pfx'} and not public_ca) or parts & {'.git', '.env', 'tests', 'test', '__pycache__', 'pytest', 'developer_tools', 'pip', 'setuptools'}:
        raise SystemExit('Forbidden release content: ' + str(path.relative_to(root)))
    if path.stat().st_size < 1024 * 1024 and b'PRIVATE KEY-----' in path.read_bytes():
        raise SystemExit('Possible private key in release: ' + str(path.relative_to(root)))
print('Distribution source/dev/secret gate passed.')
