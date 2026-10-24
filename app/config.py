import os
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
# Minimal dotenv loader; process environment always wins.
if (ROOT / '.env').exists():
    for line in (ROOT / '.env').read_text('utf-8').splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
DATA = Path(os.getenv('DATA_DIR', str(ROOT / 'data'))).resolve()
DATA.mkdir(parents=True, exist_ok=True)
UPLOADS = Path(os.getenv('UPLOAD_DIR', str(DATA / 'uploads'))).resolve()
IMAGES = Path(os.getenv('GENERATED_IMAGE_DIR', str(DATA / 'images'))).resolve()
UPLOADS.mkdir(parents=True, exist_ok=True)
IMAGES.mkdir(parents=True, exist_ok=True)
DB_URL = os.getenv('DATABASE_URL', f'sqlite:///{DATA / "campus.db"}')
PUBLIC_URL = os.getenv('PUBLIC_BASE_URL', 'http://127.0.0.1:15001').rstrip('/')
VERIFY_URL = os.getenv('VERIFY_BASE_URL', 'http://127.0.0.1:15002').rstrip('/')
SECURE = os.getenv('COOKIE_SECURE', 'false').lower() == 'true'
TRUSTED_PROXIES = [s.strip() for s in os.getenv('TRUSTED_PROXIES', '').split(',') if s.strip()]
TZ = ZoneInfo('Asia/Shanghai')
ISSUER = '示例签发单位'
def limit(name, default):
    return int(os.getenv(name, default))
