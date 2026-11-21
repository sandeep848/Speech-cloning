"""Revocable shared-token sessions; token holders share one trusted workspace."""
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

from app.config import local_development
from app.db.connection import connect


def session_signature(stamp):
    return hmac.new(os.environ.get('API_TOKEN', '').encode(), stamp.encode(), hashlib.sha256).hexdigest()


def _database():
    root = Path(os.getenv('VOICE_STUDIO_DATA', Path(__file__).resolve().parents[2] / 'data'))
    root.mkdir(parents=True, exist_ok=True)
    return root / 'studio.sqlite3'


def _sessions():
    return connect(_database())


def create_session():
    value = secrets.token_urlsafe(32)
    ttl = int(os.getenv('SESSION_TTL_SECONDS', '43200'))
    with _sessions() as c:
        c.execute('CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, expires REAL, token_signature TEXT)')
        c.execute('DELETE FROM sessions WHERE expires <= ?', (time.time(),))
        c.execute('INSERT INTO sessions VALUES (?,?,?)',
                  (hashlib.sha256(value.encode()).hexdigest(), time.time() + ttl, session_signature(value)))
    return value, ttl


def revoke_session(value):
    with _sessions() as c:
        c.execute('CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, expires REAL, token_signature TEXT)')
        c.execute('DELETE FROM sessions WHERE digest=?', (hashlib.sha256(value.encode()).hexdigest(),))


def authenticated(request):
    token = os.getenv('API_TOKEN')
    if not token:
        host = request.client.host if getattr(request, 'client', None) else ''
        return local_development() and host in ('127.0.0.1', '::1', 'testclient')
    if hmac.compare_digest(request.headers.get('authorization', ''), 'Bearer ' + token):
        return True
    value = request.cookies.get('studio_session', '')
    if not value or len(value) > 128:
        return False
    with _sessions() as c:
        c.execute('CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, expires REAL, token_signature TEXT)')
        row = c.execute('SELECT expires,token_signature FROM sessions WHERE digest=?',
                        (hashlib.sha256(value.encode()).hexdigest(),)).fetchone()
    return bool(row and row['expires'] > time.time() and hmac.compare_digest(row['token_signature'], session_signature(value)))
