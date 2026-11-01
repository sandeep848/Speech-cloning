"""Server-side accounts, revocable opaque sessions and resource authorization.

No credentials are stored in the browser bundle. Legacy API_TOKEN identifies one
shared studio principal; separately provisioned accounts identify individual users.
"""

from __future__ import annotations

from contextvars import ContextVar
import hashlib
import hmac
import os
import json
import logging
import secrets
import sqlite3
import threading
import time

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import HTTPException

principal = ContextVar("studio_principal", default=None)
passwords = PasswordHasher()  # Argon2id with independent random salts.
DUMMY_HASH = passwords.hash(secrets.token_urlsafe(32))
SESSION_SECONDS = 3600
PASSWORD_SLOTS = threading.BoundedSemaphore(4)


def production():
    return os.getenv("APP_ENV", "development") == "production"


def validate_configuration():
    mode = os.getenv("APP_ENV", "development")
    if mode not in ("development", "production", "test"):
        raise RuntimeError("APP_ENV must be development, production or test")
    if os.getenv("INFERENCE_ISOLATION", "process") not in ("process", "thread"):
        raise RuntimeError("INFERENCE_ISOLATION must be process or thread")
    ranges = {
        "MAX_PENDING_JOBS": (1, 128),
        "MAX_USER_JOBS": (1, 128),
        "MAX_OUTPUT_BYTES": (1024, 536870912),
        "MAX_OUTPUT_SECONDS": (1, 3600),
        "GENERATION_TIMEOUT_SECONDS": (0.1, 3600),
        "EVALUATION_TIMEOUT_SECONDS": (0.1, 3600),
    }
    for name, (low, high) in ranges.items():
        if name in os.environ:
            try:
                value = float(os.environ[name])
            except ValueError:
                raise RuntimeError(name + " must be numeric") from None
            if not low <= value <= high:
                raise RuntimeError(name + " is outside the supported range")
    if production():
        if os.getenv("INFERENCE_ISOLATION", "process") != "process":
            raise RuntimeError("Production requires process-isolated inference")
        if os.getenv("DEV_ALLOW_ANONYMOUS") == "1":
            raise RuntimeError(
                "Anonymous development access is forbidden in production"
            )
        if os.getenv("COOKIE_SECURE") != "1":
            raise RuntimeError("Production requires COOKIE_SECURE=1 and HTTPS")
        if os.getenv("AUTH_MODE", "token") not in ("token", "accounts"):
            raise RuntimeError("AUTH_MODE must be token or accounts")
        if (
            os.getenv("AUTH_MODE", "token") == "token"
            and len(os.getenv("API_TOKEN", "")) < 32
        ):
            raise RuntimeError(
                "Production token authentication requires API_TOKEN of at least 32 characters"
            )
        if os.getenv("AUTH_MODE") == "accounts" and not os.getenv("AUTH_EPOCH"):
            raise RuntimeError(
                "Account authentication requires AUTH_EPOCH for session rotation"
            )
        if os.getenv("ADMIN_TOKEN") and (
            len(os.environ["ADMIN_TOKEN"]) < 32
            or os.getenv("ADMIN_TOKEN") == os.getenv("API_TOKEN")
        ):
            raise RuntimeError(
                "ADMIN_TOKEN must be distinct and at least 32 characters"
            )
        origins = os.getenv("CORS_ORIGINS", "").split(",")
        if not origins or any(
            not x.strip().startswith("https://") or "*" in x for x in origins
        ):
            raise RuntimeError("Production requires explicit HTTPS CORS_ORIGINS")


def initialize(connection):
    connection.executescript("""
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
    CREATE TABLE IF NOT EXISTS rate_limits (
        key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS rate_limits_expiry ON rate_limits(expires);
    CREATE TABLE IF NOT EXISTS security_events (
        id INTEGER PRIMARY KEY, created REAL NOT NULL, kind TEXT NOT NULL,
        request_id TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied REAL NOT NULL);
    INSERT OR IGNORE INTO schema_migrations VALUES (1, strftime('%s','now'));
    """)


def owner():
    value = principal.get()
    if value is None:
        raise HTTPException(401, "Unauthorized")
    return value


def claim(connection, kind, resource_id):
    connection.execute(
        "INSERT INTO resource_owners VALUES (?,?,?)", (kind, resource_id, owner())
    )


def require_owner(connection, kind, resource_id):
    row = connection.execute(
        "SELECT 1 FROM resource_owners WHERE kind=? AND resource_id=? AND owner_id=?",
        (kind, resource_id, owner()),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Resource not found")


def version(connection, user):
    if user == "legacy":
        if os.getenv("AUTH_MODE", "token") != "token":
            return None
        token = os.getenv("API_TOKEN", "")
        return hashlib.sha256(token.encode()).hexdigest() if token else None
    if os.getenv("AUTH_MODE", "token") != "accounts":
        return None
    row = connection.execute(
        "SELECT credential_version,disabled FROM accounts WHERE id=?", (user,)
    ).fetchone()
    if not row or row["disabled"]:
        return None
    return hashlib.sha256(
        f"{os.getenv('AUTH_EPOCH', '')}:{row['credential_version']}".encode()
    ).hexdigest()


def session_value(request):
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Session "):
        return authorization[8:]
    return request.cookies.get("studio_session", "")


def identify(request, connection):
    token = (
        os.getenv("API_TOKEN", "") if os.getenv("AUTH_MODE", "token") == "token" else ""
    )
    if token and hmac.compare_digest(
        request.headers.get("authorization", "").encode(), ("Bearer " + token).encode()
    ):
        return "legacy", False
    cookie = session_value(request)
    if cookie:
        row = connection.execute(
            "SELECT * FROM sessions WHERE digest=?",
            (hashlib.sha256(cookie.encode()).hexdigest(),),
        ).fetchone()
        if row and row["expires"] > time.time():
            current = version(connection, row["owner_id"])
            if current and hmac.compare_digest(row["credential"], current):
                return row["owner_id"], not request.headers.get(
                    "authorization", ""
                ).startswith("Session ")
    if (
        os.getenv("AUTH_MODE", "token") == "token"
        and not token
        and not production()
        and os.getenv("DEV_ALLOW_ANONYMOUS") == "1"
    ):
        return "development", False
    return None, False


def authenticate(connection, token="", username="", password=""):
    user = None
    if os.getenv("AUTH_MODE", "token") == "token":
        expected = os.getenv("API_TOKEN", "")
        if expected and hmac.compare_digest(token.encode(), expected.encode()):
            user = "legacy"
    else:
        row = connection.execute(
            "SELECT * FROM accounts WHERE username=?", (username.casefold().strip(),)
        ).fetchone()
        if not PASSWORD_SLOTS.acquire(blocking=False):
            raise HTTPException(429, "Authentication capacity reached; retry later")
        try:
            try:
                valid = passwords.verify(
                    row["password_hash"] if row else DUMMY_HASH, password
                )
            except (VerificationError, InvalidHashError):
                valid = False
        finally:
            PASSWORD_SLOTS.release()
        if row and valid and not row["disabled"]:
            user = row["id"]
    if user is None:
        raise HTTPException(401, "Invalid credentials")
    cookie = secrets.token_urlsafe(48)
    connection.execute("DELETE FROM sessions WHERE expires <= ?", (time.time(),))
    connection.execute(
        "DELETE FROM sessions WHERE digest IN (SELECT digest FROM sessions WHERE owner_id=? ORDER BY expires DESC LIMIT -1 OFFSET 9)",
        (user,),
    )
    connection.execute(
        "INSERT INTO sessions VALUES (?,?,?,?)",
        (
            hashlib.sha256(cookie.encode()).hexdigest(),
            user,
            time.time() + SESSION_SECONDS,
            version(connection, user),
        ),
    )
    return cookie


class RateLimiter:
    """Atomic SQLite limits survive service restarts; keys are privacy hashes."""

    def __init__(self):
        self.database = None

    def bind(self, database):
        self.database = database

    def clear(self):
        if self.database is not None:
            with sqlite3.connect(self.database) as c:
                c.execute("DELETE FROM rate_limits")

    def blocked(self, key, maximum):
        digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()
        with sqlite3.connect(self.database, timeout=5) as c:
            row = c.execute(
                "SELECT count FROM rate_limits WHERE key=? AND expires>?",
                (digest, time.time()),
            ).fetchone()
            return bool(row and row[0] >= maximum)

    def allow(self, key, maximum, seconds=60):
        if self.database is None:
            raise RuntimeError("Rate limiter database is not initialized")
        digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()
        now = time.time()
        try:
            with sqlite3.connect(self.database, timeout=5) as c:
                c.execute("BEGIN IMMEDIATE")
                c.execute("DELETE FROM rate_limits WHERE expires<=?", (now,))
                existing = c.execute(
                    "SELECT count FROM rate_limits WHERE key=?", (digest,)
                ).fetchone()
                if (
                    not existing
                    and c.execute("SELECT count(*) FROM rate_limits").fetchone()[0]
                    >= 10000
                ):
                    return False
                row = c.execute(
                    "INSERT INTO rate_limits VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET count=count+1 RETURNING count",
                    (digest, 1, now + seconds),
                ).fetchone()
                return row[0] <= maximum
        except sqlite3.Error:
            logging.getLogger("studio").error("Rate limit storage unavailable")
            return False


def event(connection, kind, request_id):
    connection.execute(
        "INSERT INTO security_events(created,kind,request_id) VALUES (?,?,?)",
        (time.time(), kind, request_id),
    )
    connection.execute(
        "DELETE FROM security_events WHERE id <= (SELECT COALESCE(max(id),0)-10000 FROM security_events)"
    )


limiter = RateLimiter()
