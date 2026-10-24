"""Stop both application services before backup/restore. SQLite local deployment utility."""
import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from app.config import DATA,UPLOADS,IMAGES,DB_URL

parser=argparse.ArgumentParser(); parser.add_argument('destination',type=Path); args=parser.parse_args()
if not DB_URL.startswith('sqlite:///'): raise SystemExit('For PostgreSQL use pg_dump, plus private upload/image directories. See README.')
out=args.destination.resolve()
if out.exists(): raise SystemExit('Destination must not exist, to avoid overwriting a backup.')
if DATA==out or DATA in out.parents: raise SystemExit('Backup destination must be outside the runtime data directory.')
out.mkdir(parents=True)
with sqlite3.connect(DB_URL.removeprefix('sqlite:///')) as source,sqlite3.connect(out/'campus.db') as target: source.backup(target)
shutil.copytree(UPLOADS,out/'uploads'); shutil.copytree(IMAGES,out/'images')
(out/'BACKUP.txt').write_text('Offline consistent backup; restore DB, uploads and images as one set.\n'+datetime.now().isoformat(),'utf-8')
print('Backup completed. Keep this directory private.')
