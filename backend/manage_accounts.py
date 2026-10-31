"""Offline account administration. Run from backend: python manage_accounts.py --help."""
import argparse
import getpass
import hashlib
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
import os
from dotenv import load_dotenv
import security


def valid_password(password):
    if not 12 <= len(password) <= 1000 or password.casefold() in {'password1234', 'password12345', '123456789012', 'changeme12345'}:
        raise ValueError('Use a unique password of 12-1000 characters; common passwords are rejected')


def main():
    load_dotenv(Path(__file__).parent / '.env', override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['create', 'reset-token', 'disable', 'revoke', 'claim-legacy'])
    parser.add_argument('username')
    parser.add_argument('--admin', action='store_true')
    args = parser.parse_args()
    db_path = Path(os.getenv('VOICE_STUDIO_DATA', Path(__file__).parent/'data')) / 'studio.sqlite3'
    if not db_path.is_file():
        raise RuntimeError('Initialize the application database before managing accounts')
    with sqlite3.connect(db_path) as c:
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        security.initialize(c)
        name = args.username.casefold().strip()
        if not name or len(name)>100:
            raise ValueError('Invalid username')
        row = c.execute('SELECT * FROM accounts WHERE username=?', (name,)).fetchone()
        if args.action == 'create':
            password = getpass.getpass('New password: ')
            valid_password(password)
            if password != getpass.getpass('Repeat password: '):
                raise ValueError('Passwords do not match')
            user_id = str(uuid.uuid4())
            c.execute('INSERT INTO principals VALUES (?)', (user_id,))
            c.execute('INSERT INTO accounts(id,username,password_hash,role) VALUES (?,?,?,?)',
                      (user_id,name,security.passwords.hash(password),'admin' if args.admin else 'user'))
        else:
            if not row:
                raise ValueError('Unknown account')
            if args.action == 'reset-token':
                token = secrets.token_urlsafe(48)
                c.execute('DELETE FROM password_resets WHERE owner_id=?', (row['id'],))
                c.execute('INSERT INTO password_resets VALUES (?,?,?)',
                          (hashlib.sha256(token.encode()).hexdigest(), row['id'], time.time()+900))
                print('One-use reset token (expires in 15 minutes):', token)
            elif args.action == 'claim-legacy':
                # Explicit offline migration to one named owner; never infer owners.
                for kind,statement in (
                    ('reference','INSERT OR IGNORE INTO resource_owners SELECT ?,id,? FROM references_audio'),
                    ('generation','INSERT OR IGNORE INTO resource_owners SELECT ?,id,? FROM batches'),
                    ('job','INSERT OR IGNORE INTO resource_owners SELECT ?,id,? FROM jobs'),
                    ('batch','INSERT OR IGNORE INTO resource_owners SELECT ?,id,? FROM batch_runs'),
                ):
                    c.execute(statement, (kind,row['id']))
            else:
                c.execute('UPDATE accounts SET credential_version=credential_version+1,disabled=? WHERE id=?',
                          (1 if args.action=='disable' else row['disabled'],row['id']))
                c.execute('DELETE FROM sessions WHERE owner_id=?', (row['id'],))
    print('Account operation completed')

if __name__ == '__main__':
    main()
