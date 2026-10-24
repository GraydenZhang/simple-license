"""Initialize admin/approver once; generate random passwords unless securely injected."""
import argparse
import os
import secrets
from sqlalchemy import select
from argon2 import PasswordHasher
from app.models import Session, User, Setting
from app.config import ISSUER

def initialize(credentials):
    from app.schema_migrations import migrate
    migrate()
    ph=PasswordHasher(); created=[]
    with Session() as db:
        for username,password in credentials.items():
            if not db.scalar(select(User).where(User.username==username)):
                db.add(User(username=username,password_hash=ph.hash(password),role='ADMIN' if username=='admin' else 'APPROVER')); created.append(username)
        if not db.get(Setting,'issuer_name'): db.add(Setting(key='issuer_name',value=ISSUER))
        db.commit()
    print(f'Database ready. Created {len(created)} staff account(s); existing credentials preserved.')
    return created

def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.parse_args()
    credentials={}
    generated=set()
    for username in ('admin','approver'):
        supplied=os.getenv('INIT_'+username.upper()+'_PASSWORD')
        credentials[username]=supplied or secrets.token_urlsafe(18)
        if not supplied: generated.add(username)
        if not 12 <= len(credentials[username]) <= 128: raise SystemExit('Initial password must have 12-128 characters.')
    created=initialize(credentials)
    # Intentionally displayed once to the initializing operator; never saved in a report/file.
    for username in created:
        if username in generated: print(f'{username}: {credentials[username]}')

if __name__=='__main__': main()
