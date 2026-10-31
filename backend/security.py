"""Server-side accounts, revocable opaque sessions and resource authorization.

No credentials are stored in the browser bundle. Legacy API_TOKEN identifies one
shared studio principal; separately provisioned accounts identify individual users.
"""
from __future__ import annotations

from contextvars import ContextVar
import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import HTTPException

principal = ContextVar('studio_principal', default=None)
passwords = PasswordHasher()  # Argon2id with independent random salts.
DUMMY_HASH = passwords.hash(secrets.token_urlsafe(32))
SESSION_SECONDS = 3600


def production():
    return os.getenv('APP_ENV', 'development') == 'production'


def validate_configuration():
    mode = os.getenv('APP_ENV', 'development')
    if mode not in ('development', 'production', 'test'):
        raise RuntimeError('APP_ENV must be development, production or test')
    if production():
        if os.getenv('DEV_ALLOW_ANONYMOUS') == '1':
            raise RuntimeError('Anonymous development access is forbidden in production')
        if os.getenv('COOKIE_SECURE') != '1':
            raise RuntimeError('Production requires COOKIE_SECURE=1 and HTTPS')
        if os.getenv('AUTH_MODE', 'token') not in ('token', 'accounts'):
            raise RuntimeError('AUTH_MODE must be token or accounts')
        if os.getenv('AUTH_MODE', 'token') == 'token' and len(os.getenv('API_TOKEN', '')) < 32:
            raise RuntimeError('Production token authentication requires API_TOKEN of at least 32 characters')
        if os.getenv('AUTH_MODE') == 'accounts' and not os.getenv('AUTH_EPOCH'):
            raise RuntimeError('Account authentication requires AUTH_EPOCH for session rotation')
        if os.getenv('ADMIN_TOKEN') and (len(os.environ['ADMIN_TOKEN']) < 32 or os.getenv('ADMIN_TOKEN') == os.getenv('API_TOKEN')):
            raise RuntimeError('ADMIN_TOKEN must be distinct and at least 32 characters')
        origins = os.getenv('CORS_ORIGINS', '').split(',')
        if not origins or any(not x.strip().startswith('https://') or '*' in x for x in origins):
            raise RuntimeError('Production requires explicit HTTPS CORS_ORIGINS')


def initialize(connection):
    connection.executescript('''
    CREATE TABLE IF NOT EXISTS principals (id TEXT PRIMARY KEY);
    INSERT OR IGNORE INTO principals VALUES ('legacy');
    INSERT OR IGNORE INTO principals VALUES ('development');
    CREATE TABLE IF NOT EXISTS accounts (
        id TEXT PRIMARY KEY REFERENCES principals(id),
        username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('user','admin')),
        credential_version INTEGER NOT NULL DEFAULT 1, disabled INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS sessions (
        digest TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES principals(id),
        expires REAL NOT NULL, credential TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS password_resets (
        digest TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES accounts(id), expires REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires);
    CREATE TABLE IF NOT EXISTS resource_owners (
        kind TEXT NOT NULL, resource_id TEXT NOT NULL,
        owner_id TEXT NOT NULL REFERENCES principals(id),
        PRIMARY KEY(kind, resource_id)
    );
    CREATE INDEX IF NOT EXISTS resource_owner_index ON resource_owners(owner_id, kind);
    CREATE TABLE IF NOT EXISTS user_preferences (
        owner_id TEXT PRIMARY KEY REFERENCES principals(id), value TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS user_reference_profiles (
        owner_id TEXT NOT NULL REFERENCES principals(id), language TEXT NOT NULL,
        reference_id TEXT NOT NULL REFERENCES references_audio(id),
        transcript TEXT NOT NULL, PRIMARY KEY(owner_id, language)
    );
    CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied REAL NOT NULL);
    INSERT OR IGNORE INTO schema_migrations VALUES (1, strftime('%s','now'));
    ''')


def owner():
    value = principal.get()
    if value is None:
        raise HTTPException(401, 'Unauthorized')
    return value


def claim(connection, kind, resource_id):
    connection.execute('INSERT INTO resource_owners VALUES (?,?,?)', (kind, resource_id, owner()))


def require_owner(connection, kind, resource_id):
    row = connection.execute('SELECT 1 FROM resource_owners WHERE kind=? AND resource_id=? AND owner_id=?',
                             (kind, resource_id, owner())).fetchone()
    if not row:
        raise HTTPException(404, 'Resource not found')


def version(connection, user):
    if user == 'legacy':
        if os.getenv('AUTH_MODE', 'token') != 'token':
            return None
        token = os.getenv('API_TOKEN', '')
        return hashlib.sha256(token.encode()).hexdigest() if token else None
    if os.getenv('AUTH_MODE', 'token') != 'accounts':
        return None
    row = connection.execute('SELECT credential_version,disabled FROM accounts WHERE id=?', (user,)).fetchone()
    if not row or row['disabled']:
        return None
    return hashlib.sha256(f"{os.getenv('AUTH_EPOCH', '')}:{row['credential_version']}".encode()).hexdigest()


def identify(request, connection):
    token = os.getenv('API_TOKEN', '') if os.getenv('AUTH_MODE', 'token') == 'token' else ''
    if token and hmac.compare_digest(request.headers.get('authorization', ''), 'Bearer ' + token):
        return 'legacy', False
    cookie = request.cookies.get('studio_session', '')
    if cookie:
        row = connection.execute('SELECT * FROM sessions WHERE digest=?', (hashlib.sha256(cookie.encode()).hexdigest(),)).fetchone()
        if row and row['expires'] > time.time():
            current = version(connection, row['owner_id'])
            if current and hmac.compare_digest(row['credential'], current):
                return row['owner_id'], True
    if os.getenv('AUTH_MODE', 'token') == 'token' and not token and not production() and os.getenv('DEV_ALLOW_ANONYMOUS') == '1':
        return 'development', False
    return None, False


def authenticate(connection, token='', username='', password=''):
    user = None
    if os.getenv('AUTH_MODE', 'token') == 'token':
        expected = os.getenv('API_TOKEN', '')
        if expected and hmac.compare_digest(token, expected):
            user = 'legacy'
    else:
        row = connection.execute('SELECT * FROM accounts WHERE username=?', (username.casefold().strip(),)).fetchone()
        try:
            valid = passwords.verify(row['password_hash'] if row else DUMMY_HASH, password)
        except (VerificationError, InvalidHashError):
            valid = False
        if row and valid and not row['disabled']:
            user = row['id']
    if user is None:
        raise HTTPException(401, 'Invalid credentials')
    cookie = secrets.token_urlsafe(48)
    connection.execute('DELETE FROM sessions WHERE expires <= ?', (time.time(),))
    connection.execute('INSERT INTO sessions VALUES (?,?,?,?)',
                       (hashlib.sha256(cookie.encode()).hexdigest(), user, time.time()+SESSION_SECONDS, version(connection, user)))
    return cookie


class RateLimiter:
    """Bounded per-process buckets; deployment is explicitly one worker."""
    def __init__(self):
        self.buckets = OrderedDict()
        self.lock = threading.Lock()

    def allow(self, key, maximum, seconds=60):
        now = time.monotonic()
        with self.lock:
            count, until = self.buckets.pop(key, (0, now + seconds))
            if until <= now:
                count, until = 0, now + seconds
            self.buckets[key] = (count + 1, until)
            while len(self.buckets) > 10000:
                self.buckets.popitem(last=False)
            return count < maximum


limiter = RateLimiter()
